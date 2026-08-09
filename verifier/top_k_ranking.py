"""Claim-type verifier for ``ClaimType.RANKING_TOP_K``.

Registered in ``verifier.verifier.CLAIM_VERIFIERS``. Status updates go through
``verifier.base`` only.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import Engine

from planner.schemas import Claim, Evidence
from verifier.base import (
    fail,
    finalize_claim,
    is_failed,
    mark_fragile,
    pass_check,
    run_checks,
)
from verifier.context import VerificationContext, build_context
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import (
    filter_literals,
    limit_value,
    order_direction,
    ordered_columns,
)


def verify(
    claim: Claim,
    context: VerificationContext,
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

    for evidence_id in claim.evidence_ids:
        replay = context.replays.get(evidence_id)
        if replay is None:
            return fail(
                result,
                check="evidence_refs",
                reason=f"claim references unknown evidence id: {evidence_id}",
            )
        if replay.error or replay.query is None or replay.rows is None:
            return fail(
                result,
                check="sql_safety",
                reason=f"evidence {evidence_id} is not replayable: {replay.error}",
            )
        e = replay.evidence

        direction = _check_sql_shape(k, claim.metric, e, replay.query, result)
        if is_failed(result):
            return result

        rows = replay.rows
        under_k = len(rows) < k
        metric_idx = _metric_column_index(claim, e)

        steps = [
            lambda: _check_row_count(k, rows, e, result),
            lambda: _check_null_subjects(rows, e, result),
            lambda: _check_subjects(claim, rows, e, result, under_k=under_k),
        ]
        if metric_idx is not None:
            idx = metric_idx
            steps.extend(
                [
                    lambda: _check_monotonic(rows, idx, direction, e, result),
                    lambda: _check_ties(rows, idx, e, result),
                    lambda: _check_non_negative(rows, idx, e, result),
                ]
            )
        steps.append(lambda: _check_filters(claim, replay.query, e, result))

        if is_failed(run_checks(result, *steps)):
            return result

    return finalize_claim(result)


def verify_top_k_ranking(
    claim: Claim,
    evidence: list[Evidence],
    engine: Engine,
    result: ClaimVerification,
) -> ClaimVerification:
    """Backward-compatible direct entry point; production uses shared context."""
    context = build_context(
        evidence,
        engine,
        referenced_ids=set(claim.evidence_ids),
    )
    return verify(claim, context, result)


def _check_sql_shape(
    k: int,
    metric: str | None,
    evidence: Evidence,
    query: Any,
    result: ClaimVerification,
) -> Literal["ASC", "DESC"]:
    direction = order_direction(query)
    if direction is None:
        fail(
            result,
            check="top_k_sql_shape",
            reason=(
                f"Evidence {evidence.id} SQL missing ORDER BY "
                f"(required for ranking)"
            ),
        )
        return "ASC"

    actual_limit = limit_value(query)
    if actual_limit is None:
        fail(
            result,
            check="top_k_sql_shape",
            reason=(
                f"Evidence {evidence.id} SQL missing LIMIT "
                f"(expected LIMIT {k})"
            ),
        )
        return "ASC"

    if actual_limit != k:
        fail(
            result,
            check="top_k_sql_shape",
            reason=(
                f"Evidence {evidence.id} SQL LIMIT {actual_limit} "
                f"does not match claim k={k}"
            ),
        )
        return "ASC"
    if metric and metric.casefold() not in ordered_columns(query):
        fail(
            result,
            check="top_k_sql_shape",
            reason=(
                f"Evidence {evidence.id} SQL must ORDER BY metric "
                f"{metric!r}"
            ),
        )
        return "ASC"

    pass_check(result, "top_k_sql_shape")
    return direction


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
    claim: Claim,
    query: Any,
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    if not claim.filters:
        return pass_check(result, "top_k_filters")

    literals = filter_literals(query)
    missing = [
        f"{key}={value!r}"
        for key, value in claim.filters.items()
        if str(value).casefold() not in literals
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
