"""Claim-type verifier for ``ClaimType.RANKING_TOP_K``.

Registered in ``verifier.verifier.CLAIM_VERIFIERS``. Status updates go through
``verifier.base`` only.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from sqlalchemy import Engine, text

from logger import logger
from planner.schemas import Claim, Evidence
from verifier.base import (
    fail,
    finalize_claim,
    is_failed,
    mark_fragile,
    pass_check,
    run_checks,
)
from verifier.schemas import ClaimVerification

_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
_ORDER_DIR_RE = re.compile(
    r"\border\s+by\b[\s\S]*?\b(asc|desc)\b",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)


def verify(
    claim: Claim,
    evidence: list[Evidence],
    engine: Engine,
    result: ClaimVerification,
) -> ClaimVerification:
    """Run ranking-specific checks; mutate and return ``result``."""
    k = claim.k
    if not k:
        return fail(
            result,
            check="top_k_k",
            reason="top-k ranking claim has no k value",
        )

    evidence_by_id = {e.id: e for e in evidence}
    for evidence_id in claim.evidence_ids:
        e = evidence_by_id.get(evidence_id)
        if e is None:
            return fail(
                result,
                check="evidence_refs",
                reason=f"claim references unknown evidence id: {evidence_id}",
            )

        direction = _check_sql_shape(k, e, result)
        if is_failed(result):
            return result

        with engine.connect() as conn:
            rows = [list(row) for row in conn.execute(text(e.sql)).fetchall()]
            logger.trace(f"SQL replay rows:\n{rows}")

            under_k = len(rows) < k
            metric_idx = _metric_column_index(claim, e)

            steps = [
                lambda: _check_row_count(k, rows, e, result),
                lambda: _check_null_subjects(rows, e, result),
                lambda: _check_subjects(
                    claim, rows, e, result, under_k=under_k
                ),
            ]
            if metric_idx is not None:
                idx = metric_idx
                steps.extend(
                    [
                        lambda: _check_monotonic(
                            rows, idx, direction, e, result
                        ),
                        lambda: _check_ties(rows, idx, e, result),
                        lambda: _check_non_negative(rows, idx, e, result),
                    ]
                )
            steps.append(lambda: _check_filters(claim, e, result))

            if is_failed(run_checks(result, *steps)):
                return result

    return finalize_claim(result)


# Back-compat alias for callers/tests that used the old name.
verify_top_k_ranking = verify


def _order_direction(sql: str) -> Literal["ASC", "DESC"]:
    match = _ORDER_DIR_RE.search(sql)
    if match and match.group(1).upper() == "DESC":
        return "DESC"
    return "ASC"


def _check_sql_shape(
    k: int, evidence: Evidence, result: ClaimVerification
) -> Literal["ASC", "DESC"]:
    sql = evidence.sql or ""
    if not _ORDER_BY_RE.search(sql):
        fail(
            result,
            check="top_k_sql_shape",
            reason=(
                f"Evidence {evidence.id} SQL missing ORDER BY "
                f"(required for ranking)"
            ),
        )
        return "ASC"

    limit_match = _LIMIT_RE.search(sql)
    if not limit_match:
        fail(
            result,
            check="top_k_sql_shape",
            reason=(
                f"Evidence {evidence.id} SQL missing LIMIT "
                f"(expected LIMIT {k})"
            ),
        )
        return "ASC"

    limit_value = int(limit_match.group(1))
    if limit_value != k:
        fail(
            result,
            check="top_k_sql_shape",
            reason=(
                f"Evidence {evidence.id} SQL LIMIT {limit_value} "
                f"does not match claim k={k}"
            ),
        )
        return "ASC"

    pass_check(result, "top_k_sql_shape")
    return _order_direction(sql)


def _check_row_count(
    k: int,
    rows: list[list[Any]],
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    actual = len(rows)
    if actual != k:
        return mark_fragile(
            result,
            check="top_k_row_count",
            note=f"top_k_row_count expected {k} rows, got {actual}",
        )
    return pass_check(result, "top_k_row_count")


# Test helper alias (over-k soft path exercised directly in unit tests).
_check_top_k_row_count = _check_row_count


def _check_null_subjects(
    rows: list[list[Any]], evidence: Evidence, result: ClaimVerification
) -> ClaimVerification:
    for i, row in enumerate(rows):
        if not row or row[0] is None:
            return fail(
                result,
                check="top_k_null_subject",
                reason=(
                    f"NULL subject at rank {i + 1} in evidence {evidence.id}"
                ),
            )
    return pass_check(result, "top_k_null_subject")


def _check_subjects(
    claim: Claim,
    rows: list[list[Any]],
    evidence: Evidence,
    result: ClaimVerification,
    *,
    under_k: bool,
) -> ClaimVerification:
    if claim.subject is None:
        return fail(
            result,
            check="top_k_subject",
            reason=f"Ranking claim {claim.id} has no subject",
        )

    subjects = claim.subject if isinstance(claim.subject, list) else [claim.subject]
    actual_subjects = [row[0] for row in rows if row]

    if under_k:
        if len(subjects) > len(actual_subjects):
            return fail(
                result,
                check="top_k_subject",
                reason=(
                    f"Subject list length {len(subjects)} exceeds "
                    f"replayed row count {len(actual_subjects)} "
                    f"for evidence {evidence.id}"
                ),
            )
        mismatches = [
            (i, subjects[i], actual_subjects[i])
            for i in range(len(subjects))
            if subjects[i] != actual_subjects[i]
        ]
        if mismatches:
            return fail(
                result,
                check="top_k_subject",
                reason=(
                    f"Subject order mismatch in evidence {evidence.id}: "
                    f"{mismatches!r}"
                ),
            )
        return pass_check(result, "top_k_subject")

    if len(subjects) != len(actual_subjects):
        return fail(
            result,
            check="top_k_subject",
            reason=(
                f"Subject list length {len(subjects)} does not match "
                f"replayed row count {len(actual_subjects)} "
                f"for evidence {evidence.id}"
            ),
        )

    mismatches = [
        (i, subjects[i], actual_subjects[i])
        for i in range(len(subjects))
        if subjects[i] != actual_subjects[i]
    ]
    if mismatches:
        return fail(
            result,
            check="top_k_subject",
            reason=(
                f"Subject order mismatch in evidence {evidence.id}: "
                f"{mismatches!r}"
            ),
        )

    return pass_check(result, "top_k_subject")


def _metric_column_index(claim: Claim, evidence: Evidence) -> int | None:
    columns = evidence.columns or []
    if claim.metric and columns:
        metric_lower = claim.metric.lower()
        for i, col in enumerate(columns):
            if col.lower() == metric_lower or metric_lower in col.lower():
                return i
    if len(columns) >= 2:
        return 1
    return None


def _metric_values(rows: list[list[Any]], metric_idx: int) -> list[Any] | None:
    values: list[Any] = []
    for row in rows:
        if metric_idx >= len(row):
            return None
        values.append(row[metric_idx])
    return values


def _check_monotonic(
    rows: list[list[Any]],
    metric_idx: int,
    direction: Literal["ASC", "DESC"],
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    values = _metric_values(rows, metric_idx)
    if values is None:
        return fail(
            result,
            check="top_k_monotonic",
            reason=(
                f"Metric column index {metric_idx} out of range "
                f"for evidence {evidence.id}"
            ),
        )

    try:
        for i in range(1, len(values)):
            prev, curr = values[i - 1], values[i]
            if prev is None or curr is None:
                return fail(
                    result,
                    check="top_k_monotonic",
                    reason=(
                        f"NULL metric value in evidence {evidence.id} "
                        f"at rank {i if prev is None else i + 1}"
                    ),
                )
            if direction == "DESC":
                if curr > prev:
                    return fail(
                        result,
                        check="top_k_monotonic",
                        reason=(
                            f"Metric not non-increasing (DESC) in evidence "
                            f"{evidence.id}: {values!r}"
                        ),
                    )
            elif curr < prev:
                return fail(
                    result,
                    check="top_k_monotonic",
                    reason=(
                        f"Metric not non-decreasing (ASC) in evidence "
                        f"{evidence.id}: {values!r}"
                    ),
                )
    except TypeError:
        return fail(
            result,
            check="top_k_monotonic",
            reason=(
                f"Metric values not comparable in evidence {evidence.id}: "
                f"{values!r}"
            ),
        )

    return pass_check(result, "top_k_monotonic")


def _check_ties(
    rows: list[list[Any]],
    metric_idx: int,
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    values = _metric_values(rows, metric_idx)
    if values is None:
        return fail(
            result,
            check="top_k_ties",
            reason=(
                f"Metric column index {metric_idx} out of range "
                f"for evidence {evidence.id}"
            ),
        )

    for i in range(1, len(values)):
        if values[i - 1] is not None and values[i - 1] == values[i]:
            return mark_fragile(
                result,
                check="top_k_ties",
                note=(
                    f"top_k_ties adjacent equal scores at ranks "
                    f"{i}/{i + 1} in evidence {evidence.id}"
                ),
            )

    return pass_check(result, "top_k_ties")


def _check_non_negative(
    rows: list[list[Any]],
    metric_idx: int,
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    values = _metric_values(rows, metric_idx)
    if values is None:
        return fail(
            result,
            check="top_k_non_negative",
            reason=(
                f"Metric column index {metric_idx} out of range "
                f"for evidence {evidence.id}"
            ),
        )

    try:
        for i, value in enumerate(values):
            if value is not None and value < 0:
                return fail(
                    result,
                    check="top_k_non_negative",
                    reason=(
                        f"Negative metric value {value!r} at rank {i + 1} "
                        f"in evidence {evidence.id}"
                    ),
                )
    except TypeError:
        return fail(
            result,
            check="top_k_non_negative",
            reason=(
                f"Metric values not comparable for non-negative check "
                f"in evidence {evidence.id}: {values!r}"
            ),
        )

    return pass_check(result, "top_k_non_negative")


def _check_filters(
    claim: Claim, evidence: Evidence, result: ClaimVerification
) -> ClaimVerification:
    if not claim.filters:
        return result

    sql_lower = (evidence.sql or "").lower()
    missing = [
        f"{key}={value!r}"
        for key, value in claim.filters.items()
        if str(value).lower() not in sql_lower
    ]
    if missing:
        return fail(
            result,
            check="top_k_filters",
            reason=(
                f"Filter values not found in evidence {evidence.id} SQL: "
                f"{missing}"
            ),
        )

    return pass_check(result, "top_k_filters")
