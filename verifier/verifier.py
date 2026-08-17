"""Orchestrator: shared integrity checks + dispatch to claim-type verifiers.

To add a verifier for a new ``ClaimType``:

1. Create ``verifier/<name>.py`` with
   ``verify(claim, context, result) -> ClaimVerification``.
2. Update status only through ``verifier.base`` (``confirm``, ``refute``,
   ``inconclusive``, ``not_applicable``, ``finalize_claim``, ``is_failed``), so
   the severity policy in :data:`~verifier.outcome.GROUNDING_CHECKS` applies.
3. Register the function in ``CLAIM_VERIFIERS`` below.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from sqlalchemy import Engine

from domain_types import Claim, ClaimType, Evidence, VerificationStatus
from logger import logger
from planner.types import PlanAgentOutput
from provenance import QueryLog
from provenance.utils import fingerprint_rows
from verifier import (
    aggregation,
    comparison,
    distribution,
    existence,
    top_k_ranking,
    trend,
    value_lookup,
)
from verifier.base import confirm, inconclusive, is_failed, refute
from verifier.context import VerificationContext, build_context
from verifier.domain_common import resolve_replay_column
from verifier.filters import reconcile_filters
from verifier.outcome import CheckOutcome
from verifier.resolve import resolve_column
from verifier.schemas import ClaimVerification, VerifiedResponse
from verifier.sql_analysis import projection_names, projects_star

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
    ClaimType.VALUE_LOOKUP: value_lookup.verify,
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
            confirm(result, check)
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
            refute(
                result,
                check="evidence_refs",
                reason=f"claim {claim.id} has empty evidence_ids",
            )
            continue

        missing = [eid for eid in claim.evidence_ids if eid not in evidence_ids]
        if missing:
            refute(
                result,
                check="evidence_refs",
                reason=(f"claim {claim.id} references unknown evidence ids: {missing}"),
            )
            continue

        confirm(result, "evidence_refs")

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
                refute(result, check="sql_safety", reason=reason)
            continue

        for result in referencing:
            confirm(result, "sql_safety")

        rows = replay.rows
        if len(rows) != item.row_count:
            reason = (
                f"Row count mismatch for evidence {evidence_id}: "
                f"expected {item.row_count}, got {len(rows)}"
            )
            for result in referencing:
                refute(result, check="row_count", reason=reason)
            continue
        for result in referencing:
            confirm(result, "row_count")

        # Row width is structural: a row narrower than the declared columns
        # cannot be read positionally, so any column reference is unsound.
        if rows and any(len(row) != len(item.columns) for row in rows):
            reason = (
                f"Evidence {evidence_id} rows are {len(rows[0])} wide but declare "
                f"{len(item.columns)} columns"
            )
            for result in referencing:
                refute(result, check="row_shape", reason=reason)
            continue
        for result in referencing:
            confirm(result, "row_shape")

        _check_declared_columns(item, replay, referencing)

        if not item.result_fingerprint:
            reason = f"Evidence {evidence_id} has no result fingerprint"
            for result in referencing:
                refute(result, check="hash", reason=reason)
            continue
        actual = fingerprint_rows(rows)
        if actual != item.result_fingerprint:
            reason = (
                f"Hash mismatch for evidence {evidence_id}: "
                f"expected {item.result_fingerprint}, got {actual}"
            )
            for result in referencing:
                refute(result, check="hash", reason=reason)
            continue

        logger.info(f"Hash verified for evidence {evidence_id}")
        for result in referencing:
            confirm(result, "hash")

    return verified


def _check_declared_columns(
    item: Evidence,
    replay: object,
    referencing: list[ClaimVerification],
) -> None:
    """Compare declared column names with the SQL projection.

    Declared names are the planner's transcription of a projection the query
    tool never returned headers for, so a mismatch is a naming defect. Column
    references are resolved against the projection anyway, which is why this
    reports fragility instead of failing: an unaliased ``MAX(price)`` has no
    alias to copy, and ``SELECT *`` has no projection list at all.
    """
    query = getattr(replay, "query", None)
    if query is None:
        return
    if projects_star(query):
        for result in referencing:
            confirm(result, "columns", detail="projection expands *; names unchecked")
        return
    names = projection_names(query)
    declared = list(item.columns or [])
    if not names:
        return
    if len(names) == len(declared) and all(
        left.casefold() == right.casefold()
        for left, right in zip(names, declared, strict=True)
    ):
        for result in referencing:
            confirm(result, "columns")
        return
    resolvable = len(declared) == len(names) and all(
        resolve_column(names, name) is not None for name in declared if name
    )
    note = (
        f"evidence {item.id} declares columns {declared} but its projection is "
        f"{names}"
        + ("; names still resolve" if resolvable else "")
    )
    for result in referencing:
        inconclusive(result, check="columns", note=note)


def verify_metrics_and_filters(
    claims: list[Claim],
    context: VerificationContext,
    verified: VerifiedResponse,
) -> VerifiedResponse:
    """Ground the claim's metric name and filter map in its cited evidence.

    Both are descriptive annotations rather than assertions about the data, so
    neither can hard-fail a claim on its own. The one exception is a filter that
    an equality predicate on the same column positively contradicts: that means
    the claim describes a scope its evidence does not have.
    """
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
            refute(
                result,
                check="sql_safety",
                reason=(
                    f"Claim {claim.id} cites no evidence that parsed into a query"
                ),
            )
            continue

        _verify_metric(claim, referenced, result)
        _verify_filters(claim, referenced, result)

    return verified


def _verify_metric(
    claim: Claim,
    referenced: list[object],
    result: ClaimVerification,
) -> None:
    """Resolve the claim's metric name to a projected column."""
    if not claim.metric:
        return
    best = None
    for replay in referenced:
        match = resolve_replay_column(replay, claim.metric)  # type: ignore[arg-type]
        if match is None:
            continue
        if not match.is_approximate:
            confirm(result, "metric")
            return
        best = match
    if best is not None:
        inconclusive(
            result,
            check="metric",
            note=(
                f"metric {claim.metric!r} matches projected column {best.name!r} "
                f"only approximately"
            ),
        )
        return
    inconclusive(
        result,
        check="metric",
        note=(
            f"metric {claim.metric!r} does not resolve to a projected column of "
            f"the cited evidence"
        ),
    )


def _verify_filters(
    claim: Claim,
    referenced: list[object],
    result: ClaimVerification,
) -> None:
    """Locate every declared filter in predicates, grouping keys, or rows."""
    if not claim.filters:
        return
    unresolved = False
    for finding in reconcile_filters(claim.filters, referenced):  # type: ignore[arg-type]
        if finding.outcome is CheckOutcome.CONFIRMED:
            continue
        if finding.outcome is CheckOutcome.REFUTED:
            refute(result, check="filters_conflict", reason=finding.detail)
            return
        unresolved = True
        inconclusive(result, check="filters", note=finding.detail)
    if not unresolved:
        confirm(result, "filters")
