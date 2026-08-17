"""SQL AST and value helpers shared by claim verifiers.

Shape helpers are **scope-aware**: ``LIMIT``, ``OFFSET``, ``ORDER BY``,
``GROUP BY``, and aggregate discovery describe the outermost query only. A
``GROUP BY`` inside a derived table does not make the statement grouped, an
``OFFSET`` inside a subquery does not make an absence proof indefinite, and a
window function's ``OVER (ORDER BY ...)`` is not the statement's ordering.

Predicate helpers are deliberately the opposite: a filter can legitimately live
in a subquery, a CTE, or a join condition, so :func:`predicate_bindings` walks
the whole tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterator

from sqlglot import exp, parse

from domain_types import Evidence

NUMERIC_TOLERANCE = Decimal("0.000001")

#: Nodes that open a new query scope. Outer-scope walks stop here.
_SCOPE_BOUNDARIES = (exp.Subquery, exp.With, exp.CTE, exp.Window)

_COMPARISONS: dict[type[exp.Expression], str] = {
    exp.EQ: "EQ",
    exp.NEQ: "NE",
    exp.GT: "GT",
    exp.GTE: "GTE",
    exp.LT: "LT",
    exp.LTE: "LTE",
    exp.Like: "LIKE",
    exp.ILike: "LIKE",
    exp.Is: "IS",
}


@dataclass(frozen=True)
class OrderKey:
    """One statement-level ``ORDER BY`` key.

    ``name`` is the projection alias or column the key resolves to, when it can
    be resolved; ``expression`` is the normalized SQL of the ordering term so an
    unaliased expression can still be matched against a projection.
    """

    name: str | None
    expression: str
    desc: bool


@dataclass(frozen=True)
class PredicateBinding:
    """A comparison found in ``WHERE`` / ``HAVING`` / ``JOIN ... ON``.

    ``column`` is ``None`` for predicates that compare two literals or whose
    left side is not a column reference.
    """

    column: str | None
    operator: str
    values: tuple[str, ...]


def parse_read_only_query(sql: str) -> exp.Query:
    """Parse exactly one PostgreSQL read query or raise ``ValueError``."""
    try:
        statements = [statement for statement in parse(sql, read="postgres") if statement]
    except Exception as exc:
        raise ValueError(f"invalid PostgreSQL SQL: {exc}") from exc
    if len(statements) != 1:
        raise ValueError("evidence SQL must contain exactly one statement")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise ValueError("evidence SQL must be a read-only query")
    forbidden = (
        exp.Alter,
        exp.Create,
        exp.Delete,
        exp.Drop,
        exp.Insert,
        exp.Into,
        exp.Merge,
        exp.Update,
    )
    if any(statement.find(node_type) is not None for node_type in forbidden):
        raise ValueError("evidence SQL contains a write operation")
    return statement


def query_root(query: exp.Query) -> exp.Query:
    """Unwrap a parenthesized top-level query."""
    node: exp.Expression = query
    while isinstance(node, exp.Subquery) and isinstance(node.this, exp.Query):
        node = node.this
    return node  # type: ignore[return-value]


def top_level_selects(query: exp.Query) -> list[exp.Select]:
    """Every ``SELECT`` in the statement's outermost scope.

    A set operation contributes one entry per branch; a plain query contributes
    one. Derived tables and CTE bodies are not included.
    """
    root = query_root(query)
    if isinstance(root, exp.Select):
        return [root]
    if isinstance(root, exp.SetOperation):
        selects: list[exp.Select] = []
        for side in (root.this, root.expression):
            if isinstance(side, exp.Query):
                selects.extend(top_level_selects(side))
        return selects
    select = root.find(exp.Select)
    return [select] if select is not None else []


def outer_select(query: exp.Query) -> exp.Select | None:
    """The leading ``SELECT`` of the statement's outermost scope."""
    selects = top_level_selects(query)
    return selects[0] if selects else None


def _outer_nodes(
    node: exp.Expression | None,
    *node_types: type[exp.Expression],
) -> Iterator[exp.Expression]:
    """Walk ``node`` without descending into nested query scopes."""
    if node is None:
        return
    for candidate in node.walk(prune=lambda item: isinstance(item, _SCOPE_BOUNDARIES)):
        if candidate is not node and isinstance(candidate, _SCOPE_BOUNDARIES):
            continue
        if isinstance(candidate, node_types):
            yield candidate


def projection_names(query: exp.Query) -> list[str]:
    """Projected column names in ``SELECT`` order, with defaults filled in.

    ``sqlglot`` reports no alias for an unaliased expression and ``"*"`` for
    ``COUNT(*)``. PostgreSQL names those columns after the function, so
    ``SELECT MAX(price)`` yields ``["max"]`` rather than ``[""]`` — otherwise a
    correct answer fails a declared-columns comparison it can never satisfy.
    """
    select = outer_select(query)
    if select is None:
        return []
    return [_projection_name(projection) for projection in select.expressions]


def _projection_name(projection: exp.Expression) -> str:
    if isinstance(projection, exp.Alias):
        return projection.alias
    if isinstance(projection, exp.Star):
        return "*"
    if isinstance(projection, exp.Column):
        return projection.name
    if isinstance(projection, exp.Func):
        return projection.sql_name().casefold()
    return projection.alias_or_name


def projects_star(query: exp.Query) -> bool:
    """Whether the outermost projection expands a ``*``."""
    select = outer_select(query)
    if select is None:
        return False
    return any(isinstance(projection, exp.Star) for projection in select.expressions)


def column_index(evidence: Evidence, column: str) -> int | None:
    """Resolve a declared evidence column case-insensitively and unambiguously."""
    matches = [
        index
        for index, name in enumerate(evidence.columns)
        if name.casefold() == column.casefold()
    ]
    return matches[0] if len(matches) == 1 else None


def aggregate_names(query: exp.Query) -> set[str]:
    """Aggregate function names used in the statement's outermost scope."""
    names: set[str] = set()
    for select in top_level_selects(query):
        for projection in select.expressions:
            for node in _outer_nodes(projection, exp.AggFunc):
                names.add(node.sql_name().upper())
        for clause in ("having", "qualify"):
            for node in _outer_nodes(select.args.get(clause), exp.AggFunc):
                names.add(node.sql_name().upper())
    return names


def has_group_by(query: exp.Query) -> bool:
    """Whether the outermost query groups rows."""
    return any(
        select.args.get("group") is not None for select in top_level_selects(query)
    )


def has_offset(query: exp.Query) -> bool:
    """Whether the statement itself skips leading rows."""
    return query_root(query).args.get("offset") is not None


def limit_value(query: exp.Query) -> int | None:
    """The statement's own ``LIMIT``, ignoring limits inside derived tables."""
    limit = query_root(query).args.get("limit")
    if limit is None:
        return None
    expression = limit.args.get("expression")
    if isinstance(expression, exp.Literal) and expression.is_int:
        return int(expression.this)
    return None


def order_keys(query: exp.Query) -> list[OrderKey]:
    """Statement-level ``ORDER BY`` keys, resolved to projection names.

    Window ordering is excluded because it is not the statement's row order.
    Ordinals (``ORDER BY 2``) and unaliased expressions (``ORDER BY SUM(v)``)
    resolve through the projection list, so ordering by the metric is
    recognized however it is written.
    """
    order = query_root(query).args.get("order")
    if order is None or not order.expressions:
        return []

    names = projection_names(query)
    select = outer_select(query)
    projections = select.expressions if select is not None else []
    by_expression: dict[str, str] = {}
    for name, projection in zip(names, projections, strict=False):
        source = projection.this if isinstance(projection, exp.Alias) else projection
        by_expression[source.sql(comments=False).casefold()] = name

    keys: list[OrderKey] = []
    for ordered in order.expressions:
        expression = ordered.this
        rendered = expression.sql(comments=False)
        desc = bool(ordered.args.get("desc"))
        name: str | None = None
        if isinstance(expression, exp.Literal) and expression.is_int:
            index = int(expression.this) - 1
            if 0 <= index < len(names):
                name = names[index]
        elif isinstance(expression, exp.Column):
            name = expression.name
        if name is None:
            name = by_expression.get(rendered.casefold())
        keys.append(
            OrderKey(
                name=name.casefold() if name else None,
                expression=rendered,
                desc=desc,
            )
        )
    return keys


def grouping_keys(query: exp.Query) -> set[str]:
    """Projection names the outermost query groups by, case-folded."""
    names = projection_names(query)
    keys: set[str] = set()
    for select in top_level_selects(query):
        group = select.args.get("group")
        if group is None:
            continue
        for expression in group.expressions:
            if isinstance(expression, exp.Literal) and expression.is_int:
                index = int(expression.this) - 1
                if 0 <= index < len(names):
                    keys.add(names[index].casefold())
            elif isinstance(expression, exp.Column):
                keys.add(expression.name.casefold())
            elif expression.alias_or_name:
                keys.add(expression.alias_or_name.casefold())
    return keys


def _literal_text(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Literal):
        return str(node.this)
    if isinstance(node, exp.Boolean):
        return str(node.this)
    if isinstance(node, exp.Cast):
        return _literal_text(node.this)
    if isinstance(node, exp.Neg):
        inner = _literal_text(node.this)
        return f"-{inner}" if inner is not None else None
    return None


def _binding_column(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Column):
        return node.name
    column = next(iter(_outer_nodes(node, exp.Column)), None)
    return column.name if isinstance(column, exp.Column) else None


def _predicate_roots(query: exp.Query) -> Iterator[exp.Expression]:
    for predicate_type in (exp.Where, exp.Having, exp.Qualify):
        yield from query.find_all(predicate_type)
    for join in query.find_all(exp.Join):
        condition = join.args.get("on")
        if condition is not None:
            yield condition


def predicate_bindings(query: exp.Query) -> list[PredicateBinding]:
    """Column/operator/value triples from every filtering position.

    Covers ``WHERE``, ``HAVING``, ``QUALIFY``, and ``JOIN ... ON`` anywhere in
    the statement, including ``IN`` lists and ``BETWEEN`` ranges, so a filter is
    found wherever the planner chose to express it.
    """
    bindings: list[PredicateBinding] = []
    for root in _predicate_roots(query):
        for node in root.walk():
            operator = _COMPARISONS.get(type(node))
            if operator is not None and isinstance(node, exp.Binary):
                left, right = node.this, node.expression
                for source, target in ((left, right), (right, left)):
                    value = _literal_text(target)
                    if value is None:
                        continue
                    bindings.append(
                        PredicateBinding(
                            column=_binding_column(source),
                            operator=operator,
                            values=(value,),
                        )
                    )
            elif isinstance(node, exp.In):
                values = tuple(
                    text
                    for item in node.expressions
                    if (text := _literal_text(item)) is not None
                )
                if values:
                    bindings.append(
                        PredicateBinding(
                            column=_binding_column(node.this),
                            operator="IN",
                            values=values,
                        )
                    )
            elif isinstance(node, exp.Between):
                low = _literal_text(node.args.get("low"))
                high = _literal_text(node.args.get("high"))
                values = tuple(text for text in (low, high) if text is not None)
                if values:
                    bindings.append(
                        PredicateBinding(
                            column=_binding_column(node.this),
                            operator="BETWEEN",
                            values=values,
                        )
                    )
    return bindings


def to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def numbers_equal(
    actual: Any,
    expected: Any,
    *,
    tolerance: Decimal = NUMERIC_TOLERANCE,
) -> bool:
    left = to_decimal(actual)
    right = to_decimal(expected)
    if left is None or right is None:
        return False
    scale = max(Decimal(1), abs(left), abs(right))
    return abs(left - right) <= tolerance * scale


def _fractional_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent >= 0:
        return 0
    return -exponent


def reported_numbers_equal(actual: Any, expected: Any) -> bool:
    """Whether ``actual`` is the number a claim reported as ``expected``.

    Derived quantities (percent deltas, ratios) are computed at full precision
    while claims report a rounded magnitude. Direction is established by a
    separate relation or trend-direction check, so ``-93.42723…`` and
    ``93.427`` are the same figure.
    """
    left = to_decimal(actual)
    right = to_decimal(expected)
    if left is None or right is None:
        return False
    if numbers_equal(left, right) or numbers_equal(abs(left), abs(right)):
        return True
    places = _fractional_places(right)
    if places == 0:
        return False
    quantum = Decimal(10) ** -places
    return abs(left).quantize(quantum, rounding=ROUND_HALF_UP) == abs(right).quantize(
        quantum, rounding=ROUND_HALF_UP
    )


def compare_numbers(left: Any, operator: str, right: Any) -> bool | None:
    lhs = to_decimal(left)
    rhs = to_decimal(right)
    if lhs is None or rhs is None:
        return None
    operations = {
        "GT": lambda: lhs > rhs,
        "GTE": lambda: lhs >= rhs,
        "LT": lambda: lhs < rhs,
        "LTE": lambda: lhs <= rhs,
        "EQ": lambda: numbers_equal(lhs, rhs),
        "NE": lambda: not numbers_equal(lhs, rhs),
    }
    operation = operations.get(operator)
    return operation() if operation else None


def derived_change(start: Any, end: Any, mode: str) -> Decimal | None:
    first = to_decimal(start)
    last = to_decimal(end)
    if first is None or last is None:
        return None
    if mode == "absolute":
        return last - first
    if mode == "percent":
        if first == 0:
            return None
        return (last - first) / abs(first) * Decimal(100)
    return None
