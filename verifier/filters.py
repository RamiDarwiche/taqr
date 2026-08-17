"""Reconciliation of a claim's ``filters`` map against its evidence.

``filters`` is a descriptive annotation: the planner names the scope it believes
it applied. The old check looked for each filter *value* among ``WHERE`` and
``HAVING`` literals only, which made three mistakes at once. It missed filters
expressed as join conditions or as a ``GROUP BY`` dimension whose group the
claim then selects; it ignored filter *keys*, so any stray literal of the right
value satisfied any filter; and a miss hard-failed the claim.

Reconciliation now searches every place a scope can legitimately be expressed,
including the replayed rows, and separates two outcomes:

* the filter cannot be located — inconclusive, the claim is fragile;
* an equality predicate on the same column selects a *different* value — a
  refutation, because the claim describes a scope the evidence does not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from verifier.context import EvidenceReplay
from verifier.domain_common import resolve_replay_column
from verifier.outcome import CheckOutcome
from verifier.resolve import (
    canonical_value,
    normalize_identifier,
    values_equal,
)
from verifier.sql_analysis import (
    PredicateBinding,
    grouping_keys,
    predicate_bindings,
)

#: Suffixes planners append to a filter key to encode a comparison operator,
#: e.g. ``order_date_gte`` for ``order_date >= ...``.
_OPERATOR_SUFFIXES = (
    "gte",
    "gt",
    "lte",
    "lt",
    "eq",
    "ne",
    "min",
    "max",
    "from",
    "to",
    "start",
    "end",
    "before",
    "after",
)

_EQUALITY_OPERATORS = frozenset({"EQ", "IN", "IS"})

_SUFFIX_PATTERN = re.compile(
    r"^(?P<base>.+?)[_\s]*(?P<suffix>" + "|".join(_OPERATOR_SUFFIXES) + r")$"
)


@dataclass(frozen=True)
class FilterFinding:
    """How one declared filter was located, or why it could not be."""

    key: str
    value: Any
    outcome: CheckOutcome
    detail: str


def filter_key_base(key: str) -> str:
    """Strip an operator suffix from a filter key.

    ``order_date_gte`` → ``order_date``; ``status`` is unchanged.
    """
    normalized = key.strip()
    match = _SUFFIX_PATTERN.match(normalized.casefold())
    if match and match.group("base"):
        return match.group("base").rstrip("_ ")
    return normalized


def _keys_match(key: str, column: str | None) -> bool:
    if not column:
        return False
    left = normalize_identifier(filter_key_base(key))
    right = normalize_identifier(column)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def filter_tokens(value: Any) -> list[Any]:
    """Expand a filter value into comparable members.

    Multi-value filters arrive as lists, or as a comma-separated string such as
    ``"EUR, CZK"`` when the planner flattened an ``IN`` list into the filter
    map. Members are compared individually so those spellings agree with
    ``IN ('CZK', 'EUR')`` as the same set, order aside.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        tokens: list[Any] = []
        for item in value:
            tokens.extend(filter_tokens(item))
        return tokens
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        if len(parts) > 1 and all(parts):
            return parts
    return [value]


def _tokens_covered(tokens: list[Any], candidates: list[Any]) -> bool:
    """Whether every token equals some candidate, with type-normalized equality."""
    if not tokens or not candidates:
        return False
    return all(
        any(values_equal(token, candidate) for candidate in candidates)
        for token in tokens
    )


def _binding_has_value(binding: PredicateBinding, value: Any) -> bool:
    return _tokens_covered(filter_tokens(value), list(binding.values))


def _equality_values(bindings: list[PredicateBinding]) -> list[Any]:
    return [
        literal
        for binding in bindings
        if binding.operator in _EQUALITY_OPERATORS
        for literal in binding.values
    ]


def reconcile_filter(
    key: str,
    value: Any,
    replays: list[EvidenceReplay],
) -> FilterFinding:
    """Locate one ``key: value`` filter in cited SQL or replayed rows."""
    bindings: list[PredicateBinding] = []
    same_column: list[PredicateBinding] = []
    for replay in replays:
        if replay.query is None:
            continue
        for binding in predicate_bindings(replay.query):
            bindings.append(binding)
            if _keys_match(key, binding.column):
                same_column.append(binding)

    for binding in same_column:
        if _binding_has_value(binding, value):
            return FilterFinding(
                key,
                value,
                CheckOutcome.CONFIRMED,
                f"{key}={value!r} matches predicate "
                f"{binding.column} {binding.operator} {binding.values}",
            )

    tokens = filter_tokens(value)
    selected = _equality_values(same_column)
    if _tokens_covered(tokens, selected):
        return FilterFinding(
            key,
            value,
            CheckOutcome.CONFIRMED,
            f"{key}={value!r} matches the set of equality predicates on "
            f"{same_column[0].column!r}: {selected}",
        )

    for binding in bindings:
        if _binding_has_value(binding, value):
            return FilterFinding(
                key,
                value,
                CheckOutcome.CONFIRMED,
                f"{value!r} appears in predicate "
                f"{binding.column or '<expression>'} {binding.operator}",
            )

    row_finding = _find_in_named_column(key, value, replays)
    if row_finding is not None:
        return row_finding

    if selected:
        return FilterFinding(
            key,
            value,
            CheckOutcome.REFUTED,
            f"claim filter {key}={value!r} conflicts with evidence, which "
            f"restricts {same_column[0].column!r} to {sorted(selected)}",
        )

    return FilterFinding(
        key,
        value,
        CheckOutcome.INCONCLUSIVE,
        f"could not locate filter {key}={value!r} in evidence predicates, "
        f"grouping keys, or replayed rows"
        + (f"; {elsewhere}" if (elsewhere := _value_elsewhere(value, replays)) else ""),
    )


def _find_in_named_column(
    key: str,
    value: Any,
    replays: list[EvidenceReplay],
) -> FilterFinding | None:
    """Locate a filter value in the replayed column its key names.

    A breakdown query (``GROUP BY status``) applies no ``WHERE`` predicate, yet a
    claim about one group is still scoped to it: the group key is in the rows.
    Requiring the value to sit in the column the filter *names* is what
    distinguishes this from finding the same value anywhere by coincidence.
    """
    for replay in replays:
        if replay.rows is None:
            continue
        match = resolve_replay_column(replay, filter_key_base(key))
        if match is None:
            continue
        if not any(
            len(row) > match.index and values_equal(row[match.index], value)
            for row in replay.rows
        ):
            continue
        groups = grouping_keys(replay.query) if replay.query is not None else set()
        grouped = match.name.casefold() in groups
        return FilterFinding(
            key,
            value,
            CheckOutcome.CONFIRMED,
            f"{key}={value!r} occurs in "
            f"{'grouping ' if grouped else ''}column {match.name!r} of "
            f"evidence {replay.evidence.id}",
        )
    return None


def _value_elsewhere(value: Any, replays: list[EvidenceReplay]) -> str | None:
    """Note the value's presence in some other column, without treating it as scope.

    Worth reporting, because it usually means the filter key is misnamed rather
    than the scope being absent — but a value that happens to appear in an
    unrelated column does not establish that the query was scoped by it.
    """
    wanted = canonical_value(value)
    for replay in replays:
        for row in replay.rows or []:
            if any(canonical_value(cell) == wanted for cell in row):
                return (
                    f"{value!r} does occur in the rows of evidence "
                    f"{replay.evidence.id}, but not in a column named like the filter"
                )
    return None


def reconcile_filters(
    filters: dict[str, Any],
    replays: list[EvidenceReplay],
) -> list[FilterFinding]:
    """Reconcile every non-null declared filter."""
    return [
        reconcile_filter(key, value, replays)
        for key, value in filters.items()
        if value is not None
    ]
