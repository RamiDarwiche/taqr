"""Resolution of claim references against replayed evidence.

The verifier used to require the planner to declare its internal wiring
exactly: a single ``subject_column``, a metric equal to a projected alias, and
``evidence.columns`` byte-identical to the SQL projection. Every such
requirement is a language-model guess, and every guess is an opportunity for a
correct answer to be reported as false.

This module resolves those references from the evidence instead, and reports
*how* each one resolved so an approximate match can be surfaced as fragility
rather than silently accepted:

* :func:`resolve_column` matches a declared column name through decreasing
  strictness (exact, case, normalized, unique substring).
* :func:`resolve_subject` searches every column *and* every run of adjacent
  text columns, so a subject like ``"Bruno Senna"`` resolves against separate
  ``forename`` / ``surname`` columns.
* :func:`values_equal` compares cell values by canonical form, so ``354`` and
  ``"354"``, ``Decimal("40.00")`` and ``40``, or ``date(2025, 1, 2)`` and
  ``"2025-01-02"`` are not read as contradictions.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from domain_types import Evidence
from verifier.sql_analysis import numbers_equal

#: Longest run of adjacent columns joined when looking for a composite subject.
MAX_COMPOSITE_WIDTH = 3

_IDENTIFIER_NOISE = re.compile(r"[^a-z0-9]+")


class MatchQuality(str, Enum):
    """How closely a reference matched, from strongest to weakest."""

    EXACT = "EXACT"
    CASE_INSENSITIVE = "CASE_INSENSITIVE"
    NORMALIZED = "NORMALIZED"
    SUBSTRING = "SUBSTRING"

    @property
    def is_approximate(self) -> bool:
        return self in {MatchQuality.NORMALIZED, MatchQuality.SUBSTRING}


@dataclass(frozen=True)
class ColumnMatch:
    """A resolved evidence column."""

    name: str
    index: int
    quality: MatchQuality

    @property
    def is_approximate(self) -> bool:
        return self.quality.is_approximate


@dataclass(frozen=True)
class SubjectMatch:
    """Where a claim subject was found in replayed rows."""

    columns: tuple[str, ...]
    indices: tuple[int, ...]
    quality: MatchQuality

    @property
    def is_composite(self) -> bool:
        return len(self.indices) > 1

    @property
    def is_approximate(self) -> bool:
        return self.quality.is_approximate

    def describe(self) -> str:
        return " || ".join(self.columns)

    def value(self, row: list[Any]) -> str:
        """Render this subject's value for ``row`` the way it was matched."""
        return _join_cells(row[index] for index in self.indices)


def normalize_identifier(name: str) -> str:
    """Collapse a column/alias name to comparable letters and digits."""
    return _IDENTIFIER_NOISE.sub("", name.strip().casefold())


def canonical_value(value: Any) -> Any:
    """Reduce a cell or literal to a form comparable across type boundaries.

    Numbers collapse to ``Decimal`` (so ``40``, ``40.0``, ``"40.00"`` agree),
    dates and timestamps to ISO text, and remaining values to case-folded,
    whitespace-collapsed text. ``bool`` stays distinct from ``0`` / ``1``
    because a boolean existence result means something different from a count.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return value
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    text = str(value).strip()
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        collapsed = " ".join(text.split()).casefold()
        # SQL renders booleans as text; keep them comparable to Python bools.
        if collapsed in {"true", "false"}:
            return collapsed == "true"
        return collapsed


def values_equal(left: Any, right: Any) -> bool:
    """Compare two values by canonical form.

    Numeric comparisons use the relative tolerance in
    :func:`verifier.sql_analysis.numbers_equal` so replay rounding does not read
    as a contradiction.
    """
    canonical_left = canonical_value(left)
    canonical_right = canonical_value(right)
    if canonical_left is None or canonical_right is None:
        return canonical_left is canonical_right
    if isinstance(canonical_left, bool) or isinstance(canonical_right, bool):
        return canonical_left is canonical_right
    if isinstance(canonical_left, Decimal) and isinstance(canonical_right, Decimal):
        return numbers_equal(canonical_left, canonical_right)
    return str(canonical_left) == str(canonical_right)


def _join_cells(cells: Any) -> str:
    return " ".join("" if cell is None else str(cell).strip() for cell in cells).strip()


def evidence_columns(
    evidence: Evidence, *, aliases: list[str] | None = None
) -> list[str]:
    """Authoritative column names for ``evidence``.

    The SQL projection wins where it is available: it is what the database
    actually returns, whereas ``evidence.columns`` is the planner's transcription
    of a projection the query tool gives no headers for. A blank projection name
    falls back to the declared one, and a ``*`` expansion or a width mismatch
    leaves the declared names in place because positions cannot be aligned.

    Specs that reference a *declared* name still resolve: see
    :func:`verifier.domain_common.resolve_replay_column`, which consults both
    lists.
    """
    declared = list(evidence.columns or [])
    if not aliases or "*" in aliases:
        return declared or list(aliases or [])
    if len(aliases) != len(declared):
        return declared or list(aliases)
    return [
        alias or declared_name
        for alias, declared_name in zip(aliases, declared, strict=True)
    ]


def resolve_column(
    columns: list[str],
    name: str,
) -> ColumnMatch | None:
    """Resolve ``name`` against ``columns`` from strictest to loosest match.

    Returns ``None`` when nothing matches or when a tier matches ambiguously,
    so an ambiguous reference is never silently bound to one of several columns.
    """
    if not name:
        return None

    exact = [index for index, column in enumerate(columns) if column == name]
    if len(exact) == 1:
        return ColumnMatch(columns[exact[0]], exact[0], MatchQuality.EXACT)

    folded = name.casefold()
    case_insensitive = [
        index for index, column in enumerate(columns) if column.casefold() == folded
    ]
    if len(case_insensitive) == 1:
        index = case_insensitive[0]
        return ColumnMatch(columns[index], index, MatchQuality.CASE_INSENSITIVE)

    normalized = normalize_identifier(name)
    if normalized:
        matches = [
            index
            for index, column in enumerate(columns)
            if normalize_identifier(column) == normalized
        ]
        if len(matches) == 1:
            index = matches[0]
            return ColumnMatch(columns[index], index, MatchQuality.NORMALIZED)

        matches = [
            index
            for index, column in enumerate(columns)
            if normalized in normalize_identifier(column)
            or normalize_identifier(column) in normalized
        ]
        if len(matches) == 1:
            index = matches[0]
            return ColumnMatch(columns[index], index, MatchQuality.SUBSTRING)
    return None


def resolve_subject(
    subject: Any,
    columns: list[str],
    rows: list[list[Any]],
    *,
    preferred_column: str | None = None,
) -> SubjectMatch | None:
    """Find where ``subject`` appears in ``rows``.

    Single columns are tried first, in declared order, with ``preferred_column``
    (a spec's ``subject_column``) promoted to the front when it resolves. Runs of
    adjacent columns are tried next, so a subject spanning ``forename`` and
    ``surname`` resolves as a composite at the same quality a single column
    would: joining two cells is how the row spells the subject, not a guess.
    """
    if subject is None or not rows or not columns:
        return None
    wanted = [subject] if not isinstance(subject, list) else list(subject)
    if not wanted:
        return None

    width = min(len(columns), min(len(row) for row in rows))
    if width <= 0:
        return None

    order = list(range(width))
    if preferred_column:
        preferred = resolve_column(columns[:width], preferred_column)
        if preferred is not None:
            order.remove(preferred.index)
            order.insert(0, preferred.index)

    for index in order:
        quality = _subject_quality(wanted, [row[index] for row in rows])
        if quality is not None:
            return SubjectMatch((columns[index],), (index,), quality)

    for span in range(2, MAX_COMPOSITE_WIDTH + 1):
        for start in range(0, width - span + 1):
            indices = tuple(range(start, start + span))
            rendered = [_join_cells(row[i] for i in indices) for row in rows]
            quality = _subject_quality(wanted, rendered)
            if quality is not None:
                return SubjectMatch(
                    tuple(columns[i] for i in indices), indices, quality
                )
    return None


def _subject_quality(wanted: list[Any], present: list[Any]) -> MatchQuality | None:
    """Strongest quality at which every wanted subject occurs in ``present``."""
    if any(value is None for value in present):
        present = [value for value in present if value is not None]
    if not present:
        return None
    if all(any(value == item for value in present) for item in wanted):
        return MatchQuality.EXACT
    if all(any(values_equal(value, item) for value in present) for item in wanted):
        return MatchQuality.NORMALIZED
    return None


def subject_list(subject: Any) -> list[Any]:
    """Normalize a claim subject to a list."""
    if subject is None:
        return []
    return list(subject) if isinstance(subject, list) else [subject]


def subject_in_literals(subject: Any, literals: list[Any]) -> bool:
    """Whether ``subject`` is pinned by a set of SQL literal values.

    A subject is often scoped by the query rather than projected: the rows of
    ``SELECT q1 ... WHERE forename = 'Bruno' AND surname = 'Senna'`` belong to
    Bruno Senna without naming him. A whole-value match counts, and so does a
    match of every whitespace-separated token, which is how a full name is
    expressed as separate predicates.
    """
    if not literals:
        return False
    canonical = [canonical_value(literal) for literal in literals]
    for item in subject_list(subject):
        if item is None:
            return False
        wanted = canonical_value(item)
        if any(known == wanted for known in canonical):
            continue
        tokens = [token for token in str(item).split() if token]
        if len(tokens) > 1 and all(
            any(known == canonical_value(token) for known in canonical)
            for token in tokens
        ):
            continue
        return False
    return True
