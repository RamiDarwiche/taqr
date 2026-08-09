"""SQL AST and value helpers shared by claim verifiers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlglot import exp, parse

from planner.schemas import Evidence

NUMERIC_TOLERANCE = Decimal("0.000001")


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


def selected_aliases(query: exp.Query) -> list[str]:
    """Return projected aliases/names in SELECT order."""
    select = query if isinstance(query, exp.Select) else query.find(exp.Select)
    if select is None:
        return []
    return [projection.alias_or_name for projection in select.expressions]


def column_index(evidence: Evidence, column: str) -> int | None:
    """Resolve a declared evidence column case-insensitively and unambiguously."""
    matches = [
        index
        for index, name in enumerate(evidence.columns)
        if name.casefold() == column.casefold()
    ]
    return matches[0] if len(matches) == 1 else None


def aggregate_names(query: exp.Query) -> set[str]:
    return {node.sql_name().upper() for node in query.find_all(exp.AggFunc)}


def has_group_by(query: exp.Query) -> bool:
    return query.find(exp.Group) is not None


def has_offset(query: exp.Query) -> bool:
    return query.find(exp.Offset) is not None


def limit_value(query: exp.Query) -> int | None:
    limit = query.args.get("limit") or query.find(exp.Limit)
    if limit is None:
        return None
    expression = limit.args.get("expression")
    if isinstance(expression, exp.Literal) and expression.is_int:
        return int(expression.this)
    return None


def order_direction(query: exp.Query) -> str | None:
    order = query.args.get("order") or query.find(exp.Order)
    if order is None or not order.expressions:
        return None
    directions = {
        "DESC" if ordered.args.get("desc") else "ASC"
        for ordered in order.expressions
    }
    return directions.pop() if len(directions) == 1 else None


def ordered_columns(query: exp.Query) -> set[str]:
    order = query.args.get("order") or query.find(exp.Order)
    if order is None:
        return set()
    aliases = selected_aliases(query)
    columns: set[str] = set()
    for ordered in order.expressions:
        expression = ordered.this
        if isinstance(expression, exp.Column):
            columns.add(expression.name.casefold())
        elif isinstance(expression, exp.Literal) and expression.is_int:
            index = int(expression.this) - 1
            if 0 <= index < len(aliases):
                columns.add(aliases[index].casefold())
        elif expression.alias_or_name:
            columns.add(expression.alias_or_name.casefold())
    return columns


def filter_literals(query: exp.Query) -> set[str]:
    """Collect literal values only from WHERE and HAVING predicates."""
    values: set[str] = set()
    for predicate_type in (exp.Where, exp.Having):
        for predicate in query.find_all(predicate_type):
            for literal in predicate.find_all(exp.Literal):
                values.add(str(literal.this).casefold())
            for boolean in predicate.find_all(exp.Boolean):
                values.add(str(boolean.this).casefold())
    return values


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
