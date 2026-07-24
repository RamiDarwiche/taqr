from __future__ import annotations

import re
from typing import Any, Literal

from sqlalchemy import Engine, text

from domain_types import VerificationStatus
from logger import logger
from planner.schemas import Claim, Evidence
from verifier.schemas import ClaimVerification

_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
_ORDER_DIR_RE = re.compile(
    r"\border\s+by\b[\s\S]*?\b(asc|desc)\b",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)


def verify_top_k_ranking(
    claim: Claim,
    evidence: list[Evidence],
    engine: Engine,
    claim_result: ClaimVerification,
) -> ClaimVerification:
    """Mutate ``result`` in place with top-k checks; return the same object."""
    k = claim.k
    if not k:
        logger.error("Top-k ranking claim has no k value")
        claim_result.status = VerificationStatus.FAILED
        claim_result.failure_reason = "top-k ranking claim has no k value"
        return claim_result

    evidence_by_id = {e.id: e for e in evidence}
    for evidence_id in claim.evidence_ids:
        e = evidence_by_id.get(evidence_id)
        if e is None:
            claim_result = _fail(
                claim_result,
                check="evidence_refs",
                reason=f"claim references unknown evidence id: {evidence_id}",
            )
            return claim_result

        shape = _check_top_k_sql_shape(k, e, claim_result)
        claim_result = shape.result
        if claim_result.status == VerificationStatus.FAILED:
            return claim_result

        with engine.connect() as conn:
            rows = [list(row) for row in conn.execute(text(e.sql)).fetchall()]
            logger.trace(f"SQL replay rows:\n{rows}")

            claim_result = _check_top_k_row_count(k, rows, e, claim_result)
            if claim_result.status == VerificationStatus.FAILED:
                return claim_result

            under_k = len(rows) < k

            claim_result = _check_top_k_null_subjects(rows, e, claim_result)
            if claim_result.status == VerificationStatus.FAILED:
                return claim_result

            claim_result = _check_top_k_subjects(
                claim, rows, e, claim_result, under_k=under_k
            )
            if claim_result.status == VerificationStatus.FAILED:
                return claim_result

            metric_idx = _metric_column_index(claim, e)
            if metric_idx is not None:
                claim_result = _check_top_k_monotonic(
                    rows, metric_idx, shape.direction, e, claim_result
                )
                if claim_result.status == VerificationStatus.FAILED:
                    return claim_result

                claim_result = _check_top_k_ties(
                    rows, metric_idx, e, claim_result
                )
                if claim_result.status == VerificationStatus.FAILED:
                    return claim_result

                claim_result = _check_top_k_non_negative(
                    rows, metric_idx, e, claim_result
                )
                if claim_result.status == VerificationStatus.FAILED:
                    return claim_result

            claim_result = _check_top_k_filters(claim, e, claim_result)
            if claim_result.status == VerificationStatus.FAILED:
                return claim_result

    if claim_result.status != VerificationStatus.FAILED:
        if claim_result.fragility_notes:
            claim_result.status = VerificationStatus.PARTIALLY_VERIFIED
        else:
            claim_result.status = VerificationStatus.VERIFIED
            claim_result.failure_reason = None
    return claim_result


class _SqlShapeResult:
    __slots__ = ("result", "direction")

    def __init__(
        self,
        result: ClaimVerification,
        direction: Literal["ASC", "DESC"] = "ASC",
    ) -> None:
        self.result = result
        self.direction = direction


def _fail(
    claim_result: ClaimVerification,
    *,
    check: str,
    reason: str,
) -> ClaimVerification:
    logger.error(reason)
    claim_result.status = VerificationStatus.FAILED
    claim_result.failure_reason = reason
    if check not in claim_result.checks:
        claim_result.checks.append(check)
    return claim_result


def _mark_fragile(
    claim_result: ClaimVerification,
    *,
    check: str,
    note: str,
) -> ClaimVerification:
    logger.debug(note)
    if claim_result.status != VerificationStatus.FAILED:
        claim_result.status = VerificationStatus.PARTIALLY_VERIFIED
    claim_result.fragility_notes.append(note)
    if check not in claim_result.checks:
        claim_result.checks.append(check)
    return claim_result


def _append_check(claim_result: ClaimVerification, check: str) -> None:
    if check not in claim_result.checks:
        claim_result.checks.append(check)


def _order_direction(sql: str) -> Literal["ASC", "DESC"]:
    match = _ORDER_DIR_RE.search(sql)
    if match and match.group(1).upper() == "DESC":
        return "DESC"
    return "ASC"


def _check_top_k_sql_shape(
    k: int, evidence: Evidence, claim_result: ClaimVerification
) -> _SqlShapeResult:
    sql = evidence.sql or ""
    if not _ORDER_BY_RE.search(sql):
        return _SqlShapeResult(
            _fail(
                claim_result,
                check="top_k_sql_shape",
                reason=(
                    f"Evidence {evidence.id} SQL missing ORDER BY "
                    f"(required for ranking)"
                ),
            )
        )

    limit_match = _LIMIT_RE.search(sql)
    if not limit_match:
        return _SqlShapeResult(
            _fail(
                claim_result,
                check="top_k_sql_shape",
                reason=(
                    f"Evidence {evidence.id} SQL missing LIMIT "
                    f"(expected LIMIT {k})"
                ),
            )
        )

    limit_value = int(limit_match.group(1))
    if limit_value != k:
        return _SqlShapeResult(
            _fail(
                claim_result,
                check="top_k_sql_shape",
                reason=(
                    f"Evidence {evidence.id} SQL LIMIT {limit_value} "
                    f"does not match claim k={k}"
                ),
            )
        )

    _append_check(claim_result, "top_k_sql_shape")
    return _SqlShapeResult(claim_result, direction=_order_direction(sql))


def _check_top_k_row_count(
    k: int, rows: list[list[Any]], evidence: Evidence, claim_result: ClaimVerification
) -> ClaimVerification:
    actual = len(rows)
    if actual < k:
        return _mark_fragile(
            claim_result,
            check="top_k_row_count",
            note=f"top_k_row_count expected {k} rows, got {actual}",
        )
    if actual > k:
        return _mark_fragile(
            claim_result,
            check="top_k_row_count",
            note=f"top_k_row_count expected {k} rows, got {actual}",
        )
    _append_check(claim_result, "top_k_row_count")
    return claim_result


def _check_top_k_null_subjects(
    rows: list[list[Any]], evidence: Evidence, claim_result: ClaimVerification
) -> ClaimVerification:
    for i, row in enumerate(rows):
        if not row or row[0] is None:
            return _fail(
                claim_result,
                check="top_k_null_subject",
                reason=(
                    f"NULL subject at rank {i + 1} in evidence {evidence.id}"
                ),
            )
    _append_check(claim_result, "top_k_null_subject")
    return claim_result


def _check_top_k_subjects(
    claim: Claim,
    rows: list[list[Any]],
    evidence: Evidence,
    claim_result: ClaimVerification,
    *,
    under_k: bool,
) -> ClaimVerification:
    if claim.subject is None:
        return _fail(
            claim_result,
            check="top_k_subject",
            reason=f"Ranking claim {claim.id} has no subject",
        )

    subjects = claim.subject if isinstance(claim.subject, list) else [claim.subject]
    actual_subjects = [row[0] for row in rows if row]

    if under_k:
        # Soft under-k already noted; verify claimed subjects as an ordered prefix.
        if len(subjects) > len(actual_subjects):
            return _fail(
                claim_result,
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
            return _fail(
                claim_result,
                check="top_k_subject",
                reason=(
                    f"Subject order mismatch in evidence {evidence.id}: "
                    f"{mismatches!r}"
                ),
            )
        _append_check(claim_result, "top_k_subject")
        return claim_result

    if len(subjects) != len(actual_subjects):
        return _fail(
            claim_result,
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
        return _fail(
            claim_result,
            check="top_k_subject",
            reason=(
                f"Subject order mismatch in evidence {evidence.id}: "
                f"{mismatches!r}"
            ),
        )

    _append_check(claim_result, "top_k_subject")
    return claim_result


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


def _metric_values(
    rows: list[list[Any]], metric_idx: int
) -> list[Any] | None:
    values: list[Any] = []
    for row in rows:
        if metric_idx >= len(row):
            return None
        values.append(row[metric_idx])
    return values


def _check_top_k_monotonic(
    rows: list[list[Any]],
    metric_idx: int,
    direction: Literal["ASC", "DESC"],
    evidence: Evidence,
    claim_result: ClaimVerification,
) -> ClaimVerification:
    values = _metric_values(rows, metric_idx)
    if values is None:
        return _fail(
            claim_result,
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
                return _fail(
                    claim_result,
                    check="top_k_monotonic",
                    reason=(
                        f"NULL metric value in evidence {evidence.id} "
                        f"at rank {i if prev is None else i + 1}"
                    ),
                )
            if direction == "DESC":
                if curr > prev:
                    return _fail(
                        claim_result,
                        check="top_k_monotonic",
                        reason=(
                            f"Metric not non-increasing (DESC) in evidence "
                            f"{evidence.id}: {values!r}"
                        ),
                    )
            elif curr < prev:
                return _fail(
                    claim_result,
                    check="top_k_monotonic",
                    reason=(
                        f"Metric not non-decreasing (ASC) in evidence "
                        f"{evidence.id}: {values!r}"
                    ),
                )
    except TypeError:
        return _fail(
            claim_result,
            check="top_k_monotonic",
            reason=(
                f"Metric values not comparable in evidence {evidence.id}: "
                f"{values!r}"
            ),
        )

    _append_check(claim_result, "top_k_monotonic")
    return claim_result


def _check_top_k_ties(
    rows: list[list[Any]],
    metric_idx: int,
    evidence: Evidence,
    claim_result: ClaimVerification,
) -> ClaimVerification:
    values = _metric_values(rows, metric_idx)
    if values is None:
        return _fail(
            claim_result,
            check="top_k_ties",
            reason=(
                f"Metric column index {metric_idx} out of range "
                f"for evidence {evidence.id}"
            ),
        )

    for i in range(1, len(values)):
        if values[i - 1] is not None and values[i - 1] == values[i]:
            return _mark_fragile(
                claim_result,
                check="top_k_ties",
                note=(
                    f"top_k_ties adjacent equal scores at ranks "
                    f"{i}/{i + 1} in evidence {evidence.id}"
                ),
            )

    _append_check(claim_result, "top_k_ties")
    return claim_result


def _check_top_k_non_negative(
    rows: list[list[Any]],
    metric_idx: int,
    evidence: Evidence,
    claim_result: ClaimVerification,
) -> ClaimVerification:
    values = _metric_values(rows, metric_idx)
    if values is None:
        return _fail(
            claim_result,
            check="top_k_non_negative",
            reason=(
                f"Metric column index {metric_idx} out of range "
                f"for evidence {evidence.id}"
            ),
        )

    try:
        for i, value in enumerate(values):
            if value is not None and value < 0:
                return _fail(
                    claim_result,
                    check="top_k_non_negative",
                    reason=(
                        f"Negative metric value {value!r} at rank {i + 1} "
                        f"in evidence {evidence.id}"
                    ),
                )
    except TypeError:
        return _fail(
            claim_result,
            check="top_k_non_negative",
            reason=(
                f"Metric values not comparable for non-negative check "
                f"in evidence {evidence.id}: {values!r}"
            ),
        )

    _append_check(claim_result, "top_k_non_negative")
    return claim_result


def _check_top_k_filters(
    claim: Claim, evidence: Evidence, claim_result: ClaimVerification
) -> ClaimVerification:
    if not claim.filters:
        return claim_result

    sql_lower = (evidence.sql or "").lower()
    missing = [
        f"{key}={value!r}"
        for key, value in claim.filters.items()
        if str(value).lower() not in sql_lower
    ]
    if missing:
        return _fail(
            claim_result,
            check="top_k_filters",
            reason=(
                f"Filter values not found in evidence {evidence.id} SQL: "
                f"{missing}"
            ),
        )

    _append_check(claim_result, "top_k_filters")
    return claim_result
