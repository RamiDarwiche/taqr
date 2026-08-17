"""Claim-type verifier for ``ClaimType.RANKING_TOP_K``.

Registered in ``verifier.verifier.CLAIM_VERIFIERS``. Status updates go through
``verifier.base`` only.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import Engine

from domain_types import Claim, Evidence
from verifier.base import (
    confirm,
    confirm_unless_recorded,
    finalize_claim,
    inconclusive,
    is_failed,
    refute,
    run_checks,
)
from verifier.context import VerificationContext, build_context
from verifier.domain_common import replay_columns, resolve_replay_column
from verifier.filters import reconcile_filters
from verifier.outcome import CheckOutcome
from verifier.resolve import resolve_column, resolve_subject, subject_list, values_equal
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import limit_value, order_keys


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    """Run ranking-specific checks; mutate and return ``result``."""
    k = claim.k
    if not k:
        return refute(
            result,
            check="top_k_k",
            reason="top-k ranking claim has no k value",
        )

    for evidence_id in claim.evidence_ids:
        replay = context.replays.get(evidence_id)
        if replay is None:
            return refute(
                result,
                check="evidence_refs",
                reason=f"claim references unknown evidence id: {evidence_id}",
            )
        if replay.error or replay.query is None or replay.rows is None:
            return refute(
                result,
                check="sql_safety",
                reason=f"evidence {evidence_id} is not replayable: {replay.error}",
            )
        e = replay.evidence

        direction = _check_sql_shape(k, claim.metric, e, replay.query, result)
        if is_failed(result):
            return result

        rows = replay.rows
        subject_index = _subject_column_index(claim, replay)
        metric_idx = _metric_column_index(claim, e, replay)

        steps = [
            lambda: _check_row_count(k, rows, e, result),
            lambda: _check_null_subjects(rows, subject_index, e, result),
            lambda: _check_subjects(claim, rows, subject_index, e, result),
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
        steps.append(lambda: _check_filters(claim, replay, e, result))

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
    """Validate that the ranking order is reproducible and by the metric.

    A ranking without a statement-level ``ORDER BY`` is not reproducible, so it
    is refuted. A ``LIMIT`` that disagrees with ``k`` is over- or under-fetching
    and is reported as fragility — the subject and row-count checks below decide
    whether the claim itself holds.

    The metric may be ordered by alias, by ordinal, or by the same expression
    the projection uses (``ORDER BY SUM(v)`` for ``SUM(v) AS total``); all three
    resolve. Ordering by a *different* known column contradicts a claim to rank
    by the metric.
    """
    keys = order_keys(query)
    if not keys:
        refute(
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
        inconclusive(
            result,
            check="top_k_sql_shape",
            note=(
                f"evidence {evidence.id} SQL has no LIMIT (expected LIMIT {k}), "
                f"so the list is bounded only by the returned rows"
            ),
        )
    elif actual_limit != k:
        inconclusive(
            result,
            check="top_k_sql_shape",
            note=(
                f"evidence {evidence.id} SQL LIMIT {actual_limit} does not match "
                f"claim k={k}"
            ),
        )

    metric_key = None
    if metric:
        folded = metric.casefold()
        metric_key = next((key for key in keys if key.name == folded), None)
        if metric_key is None and all(key.name for key in keys):
            refute(
                result,
                check="top_k_sql_shape",
                reason=(
                    f"Evidence {evidence.id} orders by "
                    f"{sorted(key.name for key in keys if key.name)} rather than "
                    f"the claimed metric {metric!r}"
                ),
            )
            return "ASC"
        if metric_key is None:
            inconclusive(
                result,
                check="top_k_sql_shape",
                note=(
                    f"evidence {evidence.id} orders by an expression that could "
                    f"not be resolved to the metric {metric!r}"
                ),
            )

    confirm_unless_recorded(result, "top_k_sql_shape")

    leading = metric_key or keys[0]
    return "DESC" if leading.desc else "ASC"


def _check_row_count(
    k: int,
    rows: list[list[Any]],
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    actual = len(rows)
    if actual != k:
        return inconclusive(
            result,
            check="top_k_row_count",
            note=f"top_k_row_count expected {k} rows, got {actual}",
        )
    return confirm(result, "top_k_row_count")


# Test helper alias (over-k soft path exercised directly in unit tests).
_check_top_k_row_count = _check_row_count


def _subject_column_index(claim: Claim, replay: Any) -> int:
    """Which column holds the ranked entity.

    Convention puts it first, but a ranking that projects an id before the name,
    or a rank ordinal before both, would make that convention wrong. When the
    claimed subjects can be located, believe the data over the convention.
    """
    if replay.rows:
        match = resolve_subject(claim.subject, replay_columns(replay), replay.rows)
        if match is not None and not match.is_composite:
            return match.indices[0]
    return 0


def _check_null_subjects(
    rows: list[list[Any]],
    subject_index: int,
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    for i, row in enumerate(rows):
        if not row or len(row) <= subject_index or row[subject_index] is None:
            return refute(
                result,
                check="top_k_null_subject",
                reason=(
                    f"NULL subject at rank {i + 1} in evidence {evidence.id}"
                ),
            )
    return confirm(result, "top_k_null_subject")


def _check_subjects(
    claim: Claim,
    rows: list[list[Any]],
    subject_index: int,
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    """Compare claimed subjects to the leading replayed rows, in order.

    The claim asserts the first ``len(subjects)`` places, so it is checked
    against that prefix: a query that returned more rows than the claim names
    still supports the claim about its leading rows. Comparison is by canonical
    value, so an integer key claimed as text is not a contradiction.
    """
    if claim.subject is None:
        return refute(
            result,
            check="top_k_subject",
            reason=f"Ranking claim {claim.id} has no subject",
        )

    subjects = subject_list(claim.subject)
    actual_subjects = [
        row[subject_index] for row in rows if len(row) > subject_index
    ]

    if len(subjects) > len(actual_subjects):
        return refute(
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
        if not values_equal(subjects[i], actual_subjects[i])
    ]
    if mismatches:
        return refute(
            result,
            check="top_k_subject",
            reason=(
                f"Subject order mismatch in evidence {evidence.id}: "
                f"{mismatches!r}"
            ),
        )
    if len(subjects) < len(actual_subjects):
        inconclusive(
            result,
            check="top_k_subject",
            note=(
                f"claim names {len(subjects)} of {len(actual_subjects)} replayed "
                f"rows in evidence {evidence.id}; only the leading rows are checked"
            ),
        )
        return result

    return confirm(result, "top_k_subject")


def _metric_column_index(
    claim: Claim,
    evidence: Evidence,
    replay: Any = None,
) -> int | None:
    """Resolve the metric column, falling back to the conventional position."""
    columns = replay_columns(replay) if replay is not None else list(evidence.columns or [])
    if claim.metric:
        match = (
            resolve_replay_column(replay, claim.metric)
            if replay is not None
            else resolve_column(columns, claim.metric)
        )
        if match is not None:
            return match.index
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
        return refute(
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
                return refute(
                    result,
                    check="top_k_monotonic",
                    reason=(
                        f"NULL metric value in evidence {evidence.id} "
                        f"at rank {i if prev is None else i + 1}"
                    ),
                )
            if direction == "DESC":
                if curr > prev:
                    return refute(
                        result,
                        check="top_k_monotonic",
                        reason=(
                            f"Metric not non-increasing (DESC) in evidence "
                            f"{evidence.id}: {values!r}"
                        ),
                    )
            elif curr < prev:
                return refute(
                    result,
                    check="top_k_monotonic",
                    reason=(
                        f"Metric not non-decreasing (ASC) in evidence "
                        f"{evidence.id}: {values!r}"
                    ),
                )
    except TypeError:
        return inconclusive(
            result,
            check="top_k_monotonic",
            note=(
                f"metric values in evidence {evidence.id} are not mutually "
                f"comparable, so ranking order could not be checked: {values!r}"
            ),
        )

    return confirm(result, "top_k_monotonic")


def _check_ties(
    rows: list[list[Any]],
    metric_idx: int,
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    values = _metric_values(rows, metric_idx)
    if values is None:
        return refute(
            result,
            check="top_k_ties",
            reason=(
                f"Metric column index {metric_idx} out of range "
                f"for evidence {evidence.id}"
            ),
        )

    for i in range(1, len(values)):
        if values[i - 1] is not None and values[i - 1] == values[i]:
            return inconclusive(
                result,
                check="top_k_ties",
                note=(
                    f"top_k_ties adjacent equal scores at ranks "
                    f"{i}/{i + 1} in evidence {evidence.id}"
                ),
            )

    return confirm(result, "top_k_ties")


def _check_non_negative(
    rows: list[list[Any]],
    metric_idx: int,
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    values = _metric_values(rows, metric_idx)
    if values is None:
        return refute(
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
                return refute(
                    result,
                    check="top_k_non_negative",
                    reason=(
                        f"Negative metric value {value!r} at rank {i + 1} "
                        f"in evidence {evidence.id}"
                    ),
                )
    except TypeError:
        return inconclusive(
            result,
            check="top_k_non_negative",
            note=(
                f"metric values in evidence {evidence.id} are not comparable to "
                f"zero: {values!r}"
            ),
        )

    return confirm(result, "top_k_non_negative")


def _check_filters(
    claim: Claim,
    replay: Any,
    evidence: Evidence,
    result: ClaimVerification,
) -> ClaimVerification:
    """Reconcile ranking filters against predicates, groups, and rows."""
    if not claim.filters:
        return confirm(result, "top_k_filters")

    unresolved = False
    for finding in reconcile_filters(claim.filters, [replay]):
        if finding.outcome is CheckOutcome.CONFIRMED:
            continue
        if finding.outcome is CheckOutcome.REFUTED:
            return refute(
                result,
                check="top_k_filters_conflict",
                reason=f"Evidence {evidence.id}: {finding.detail}",
            )
        unresolved = True
        inconclusive(
            result,
            check="top_k_filters",
            note=f"evidence {evidence.id}: {finding.detail}",
        )
    if not unresolved:
        confirm(result, "top_k_filters")
    return result
