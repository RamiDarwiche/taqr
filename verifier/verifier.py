"""Orchestrator: shared integrity checks + dispatch to claim-type verifiers.

To add a verifier for a new ``ClaimType``:

1. Create ``verifier/<name>.py`` with
   ``verify(claim, context, result) -> ClaimVerification``.
2. Use helpers from ``verifier.base`` (``fail``, ``mark_fragile``,
   ``pass_check``, ``finalize_claim``, ``is_failed``) for all status updates.
3. Register the function in ``CLAIM_VERIFIERS`` below.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from sqlalchemy import Engine

from types.common import ClaimType, VerificationStatus
from logger import logger
from planner.types import Claim, Evidence, PlanAgentOutput
from provenance import QueryLog
from provenance.utils import fingerprint_rows
from verifier import (
    aggregation,
    comparison,
    distribution,
    existence,
    top_k_ranking,
    trend,
)
from verifier.base import fail, is_failed, pass_check
from verifier.context import VerificationContext, build_context
from verifier.schemas import ClaimVerification, VerifiedResponse
from verifier.sql_analysis import filter_literals, selected_aliases

ClaimVerifier = Callable[
    [Claim, VerificationContext, ClaimVerification],
    ClaimVerification,
]

# Claim-type → specialized verifier. Shared integrity runs first in verify_response.
CLAIM_VERIFIERS: dict[ClaimType, ClaimVerifier] = {
    ClaimType.RANKING_TOP_K: top_k_ranking.verify,
    ClaimType.AGGREGATION: aggregation.verify,
    ClaimType.COMPARISON: comparison.verify,
    ClaimType.TREND: trend.verify,
    ClaimType.EXISTENCE: existence.verify,
    ClaimType.DISTRIBUTION: distribution.verify,
}


def _gate_status(claim_results: list[ClaimVerification]) -> VerificationStatus:
    if not claim_results:
        return VerificationStatus.FAILED
    statuses = {r.status for r in claim_results}
    if statuses == {VerificationStatus.VERIFIED}:
        return VerificationStatus.VERIFIED
    if VerificationStatus.FAILED in statuses:
        if (
            VerificationStatus.VERIFIED in statuses
            or VerificationStatus.PARTIALLY_VERIFIED in statuses
        ):
            return VerificationStatus.PARTIALLY_VERIFIED
        return VerificationStatus.FAILED
    if (
        VerificationStatus.VERIFIED in statuses
        or VerificationStatus.PARTIALLY_VERIFIED in statuses
    ):
        return VerificationStatus.PARTIALLY_VERIFIED
    return VerificationStatus.NOT_VERIFIED


def _fail_all(
    verified: VerifiedResponse,
    *,
    reason: str,
    checks: list[str],
) -> VerifiedResponse:
    """Fail every claim without hiding an earlier, more specific failure."""
    for result in verified.claim_results:
        for check in checks:
            pass_check(result, check)
        if not is_failed(result):
            result.status = VerificationStatus.FAILED
            result.failure_reason = reason
    verified.status = VerificationStatus.FAILED
    return verified


def verify_response(
    response: PlanAgentOutput,
    engine: Engine,
    query_log: QueryLog,
    session_id: str,
    run_id: str,
    *,
    query: str | None = None,
) -> VerifiedResponse:
    """Entry point: shared integrity checks, then per-claim type dispatch."""
    claims = response.claims
    evidence = response.evidence

    verified = VerifiedResponse(
        query=query,
        response=response,
        status=VerificationStatus.NOT_VERIFIED,
        claim_results=[
            ClaimVerification(
                claim_id=c.id,
                status=VerificationStatus.NOT_VERIFIED,
            )
            for c in claims
        ],
    )

    if not claims:
        logger.error("No claims were returned by the plan agent")
        verified.status = VerificationStatus.FAILED
        return verified

    if not evidence:
        logger.error("No evidence proposed by the plan agent")
        return _fail_all(
            verified,
            reason="No evidence provided",
            checks=[],
        )

    verified = verify_evidence_refs(claims, evidence, verified)
    referenced_ids = {
        evidence_id for claim in claims for evidence_id in claim.evidence_ids
    }
    context = build_context(evidence, engine, referenced_ids=referenced_ids)

    logger.info(f"Verifying {len(claims)} claims")

    verified = verify_replays(context, verified)
    verified = verify_metrics_and_filters(claims, context, verified)

    results_by_id = {r.claim_id: r for r in verified.claim_results}
    for claim in claims:
        result = results_by_id[claim.id]
        if is_failed(result):
            continue
        logger.info(f"Verifying claim {claim.id}: {claim.claim_text}")
        verifier_fn = CLAIM_VERIFIERS.get(claim.claim_type)
        if verifier_fn is None:
            result.status = VerificationStatus.NOT_VERIFIED
            result.failure_reason = f"No verifier for claim_type={claim.claim_type}"
            continue
        verifier_fn(claim, context, result)

    verified.status = _gate_status(verified.claim_results)
    logger.info(f"Trust gate status: {verified.status}")
    return verified


def _claim_results_for_evidence(
    verified: VerifiedResponse, evidence_id: str
) -> list[ClaimVerification]:
    """Return claim_results whose claims cite ``evidence_id`` in evidence_ids."""
    results_by_id = {r.claim_id: r for r in verified.claim_results}
    matched: list[ClaimVerification] = []
    for claim in verified.response.claims:
        if evidence_id in claim.evidence_ids:
            result = results_by_id.get(claim.id)
            if result is not None:
                matched.append(result)
    return matched


def verify_evidence_refs(
    claims: list[Claim], evidence: list[Evidence], verified: VerifiedResponse
) -> VerifiedResponse:
    """Ensure evidence identifiers form a complete, unambiguous reference graph."""
    all_ids = [item.id for item in evidence]
    evidence_ids = set(all_ids)
    results_by_id = {r.claim_id: r for r in verified.claim_results}

    duplicate_ids = sorted(
        item_id for item_id, count in Counter(all_ids).items() if count > 1
    )
    if duplicate_ids:
        return _fail_all(
            verified,
            reason=f"Duplicate evidence ids: {duplicate_ids}",
            checks=["evidence_refs"],
        )

    for claim in claims:
        result = results_by_id.get(claim.id)
        if result is None or is_failed(result):
            continue

        if not claim.evidence_ids:
            fail(
                result,
                check="evidence_refs",
                reason=f"claim {claim.id} has empty evidence_ids",
            )
            continue

        missing = [eid for eid in claim.evidence_ids if eid not in evidence_ids]
        if missing:
            fail(
                result,
                check="evidence_refs",
                reason=(f"claim {claim.id} references unknown evidence ids: {missing}"),
            )
            continue

        pass_check(result, "evidence_refs")

    referenced_ids = {item_id for claim in claims for item_id in claim.evidence_ids}
    unreferenced = sorted(evidence_ids - referenced_ids)
    if unreferenced:
        return _fail_all(
            verified,
            reason=f"Unreferenced evidence ids: {unreferenced}",
            checks=["evidence_refs"],
        )

    return verified


def verify_replays(
    context: VerificationContext,
    verified: VerifiedResponse,
) -> VerifiedResponse:
    """Validate safe replay, declared shape, row count, and fingerprint."""
    for evidence_id, replay in context.replays.items():
        item = replay.evidence
        referencing = _claim_results_for_evidence(verified, evidence_id)
        if replay.error or replay.query is None or replay.rows is None:
            reason = f"Evidence {evidence_id} is not safely replayable: {replay.error}"
            for result in referencing:
                fail(result, check="sql_safety", reason=reason)
            continue

        for result in referencing:
            pass_check(result, "sql_safety")

        rows = replay.rows
        if len(rows) != item.row_count:
            reason = (
                f"Row count mismatch for evidence {evidence_id}: "
                f"expected {item.row_count}, got {len(rows)}"
            )
            for result in referencing:
                fail(result, check="row_count", reason=reason)
            continue
        for result in referencing:
            pass_check(result, "row_count")

        if not item.columns or any(len(row) != len(item.columns) for row in rows):
            reason = f"Evidence {evidence_id} rows do not match declared columns"
            for result in referencing:
                fail(result, check="columns", reason=reason)
            continue
        aliases = selected_aliases(replay.query)
        if (
            aliases
            and "*" not in aliases
            and (
                len(aliases) != len(item.columns)
                or any(
                    alias.casefold() != column.casefold()
                    for alias, column in zip(aliases, item.columns, strict=True)
                )
            )
        ):
            reason = (
                f"Evidence {evidence_id} declared columns {item.columns} "
                f"do not match SQL projections {aliases}"
            )
            for result in referencing:
                fail(result, check="columns", reason=reason)
            continue
        for result in referencing:
            pass_check(result, "columns")

        if not item.result_fingerprint:
            reason = f"Evidence {evidence_id} has no result fingerprint"
            for result in referencing:
                fail(result, check="hash", reason=reason)
            continue
        actual = fingerprint_rows(rows)
        if actual != item.result_fingerprint:
            reason = (
                f"Hash mismatch for evidence {evidence_id}: "
                f"expected {item.result_fingerprint}, got {actual}"
            )
            for result in referencing:
                fail(result, check="hash", reason=reason)
            continue

        logger.info(f"Hash verified for evidence {evidence_id}")
        for result in referencing:
            pass_check(result, "hash")

    return verified


def verify_metrics_and_filters(
    claims: list[Claim],
    context: VerificationContext,
    verified: VerifiedResponse,
) -> VerifiedResponse:
    """Resolve metric aliases and filter literals in cited query ASTs."""
    results_by_id = {r.claim_id: r for r in verified.claim_results}

    for claim in claims:
        result = results_by_id.get(claim.id)
        if result is None or is_failed(result):
            continue

        referenced = [
            replay
            for replay in context.cited(claim.evidence_ids)
            if replay.query is not None
        ]
        if not referenced:
            fail(
                result,
                check="metric",
                reason=(
                    f"Claim {claim.id} references no known evidence for metric check"
                ),
            )
            continue

        if claim.metric:
            metric = claim.metric.casefold()
            if not any(
                metric in {alias.casefold() for alias in selected_aliases(replay.query)}
                or metric in {column.casefold() for column in replay.evidence.columns}
                for replay in referenced
            ):
                fail(
                    result,
                    check="metric",
                    reason=(
                        f"Metric {claim.metric!r} is not an exact projected alias "
                        f"for claim {claim.id}"
                    ),
                )
                continue
            pass_check(result, "metric")

        expected_filters = {
            str(value).casefold()
            for value in claim.filters.values()
            if value is not None
        }
        available_literals = {
            literal
            for replay in referenced
            for literal in filter_literals(replay.query)
        }
        missing_filters = sorted(expected_filters - available_literals)
        if missing_filters:
            fail(
                result,
                check="filters",
                reason=(
                    f"Claim filters are absent from WHERE/HAVING literals: "
                    f"{missing_filters}"
                ),
            )
            continue
        pass_check(result, "filters")

    return verified
