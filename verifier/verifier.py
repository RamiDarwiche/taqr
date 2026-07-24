"""Orchestrator: shared integrity checks + dispatch to claim-type verifiers.

To add a verifier for a new ``ClaimType``:

1. Create ``verifier/<name>.py`` with
   ``verify(claim, evidence, engine, result) -> ClaimVerification``.
2. Use helpers from ``verifier.base`` (``fail``, ``mark_fragile``,
   ``pass_check``, ``finalize_claim``, ``is_failed``) for all status updates.
3. Register the function in ``CLAIM_VERIFIERS`` below.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Engine, text

from domain_types import ClaimType, VerificationStatus
from logger import logger
from planner.schemas import Claim, Evidence, PlanAgentOutput
from provenance import QueryLog
from provenance.utils import fingerprint_rows
from verifier import top_k_ranking
from verifier.base import fail, is_failed, pass_check
from verifier.schemas import ClaimVerification, VerifiedResponse

ClaimVerifier = Callable[
    [Claim, list[Evidence], Engine, ClaimVerification],
    ClaimVerification,
]

# Claim-type → specialized verifier. Shared integrity runs first in verify_response.
CLAIM_VERIFIERS: dict[ClaimType, ClaimVerifier] = {
    ClaimType.RANKING_TOP_K: top_k_ranking.verify,
}


def _gate_status(claim_results: list[ClaimVerification]) -> VerificationStatus:
    if not claim_results:
        return VerificationStatus.FAILED
    statuses = {r.status for r in claim_results}
    if statuses == {VerificationStatus.VERIFIED}:
        return VerificationStatus.VERIFIED
    if VerificationStatus.FAILED in statuses:
        if VerificationStatus.VERIFIED in statuses:
            return VerificationStatus.PARTIALLY_VERIFIED
        return VerificationStatus.FAILED
    if VerificationStatus.VERIFIED in statuses:
        return VerificationStatus.PARTIALLY_VERIFIED
    return VerificationStatus.NOT_VERIFIED


def _fail_all(
    verified: VerifiedResponse,
    *,
    reason: str,
    checks: list[str],
) -> VerifiedResponse:
    """Fail every claim (integrity failure that compromises the whole response)."""
    for result in verified.claim_results:
        for check in checks:
            pass_check(result, check)
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

    logger.info(f"Verifying {len(claims)} claims")

    verified = verify_hashes(
        evidence, engine, verified
    )  # TODO: more granualar hashing? i.e. fingerprint each row
    verified = verify_metrics(claims, evidence, verified)

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
        verifier_fn(claim, evidence, engine, result)

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
    """Ensure each claim cites at least one evidence id that exists in ``evidence``."""
    evidence_ids = {e.id for e in evidence}
    results_by_id = {r.claim_id: r for r in verified.claim_results}

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
                reason=(
                    f"claim {claim.id} references unknown evidence ids: {missing}"
                ),
            )
            continue

        pass_check(result, "evidence_refs")

    return verified


def verify_hashes(
    evidence: list[Evidence], engine: Engine, verified: VerifiedResponse
) -> VerifiedResponse:
    """Rerun each evidence SQL and compare row fingerprints to the stored hash."""
    for e in evidence:
        referencing = _claim_results_for_evidence(verified, e.id)
        if not referencing:
            logger.error(f"Evidence {e.id} is not referenced by any claim")
            continue

        if not e.result_fingerprint or not e.sql:
            reason = f"Evidence {e.id} has no result fingerprint or SQL"
            logger.error(reason)
            logger.error(e)
            for result in referencing:
                fail(result, check="hash", reason=reason)
                pass_check(result, "row_count")
            continue

        with engine.connect() as conn:
            rows = [list(row) for row in conn.execute(text(e.sql)).fetchall()]

        if len(rows) != e.row_count:
            reason = (
                f"Row count mismatch for evidence {e.id}: "
                f"expected {e.row_count}, got {len(rows)}"
            )
            logger.error(reason)
            for result in referencing:
                fail(result, check="hash", reason=reason)
                pass_check(result, "row_count")
            continue

        actual = fingerprint_rows(rows)
        if actual != e.result_fingerprint:
            reason = (
                f"Hash mismatch for evidence {e.id}: "
                f"expected {e.result_fingerprint}, got {actual}"
            )
            logger.error(reason)
            for result in referencing:
                fail(result, check="hash", reason=reason)
                pass_check(result, "row_count")
            continue

        logger.info(f"Hash verified for evidence {e.id}")
        for result in referencing:
            pass_check(result, "hash")
            pass_check(result, "row_count")

    return verified


def verify_metrics(
    claims: list[Claim], evidence: list[Evidence], verified: VerifiedResponse
) -> VerifiedResponse:
    """Verify each claim's metric appears in at least one referenced evidence SQL."""
    results_by_id = {r.claim_id: r for r in verified.claim_results}
    evidence_by_id = {e.id: e for e in evidence}

    for claim in claims:
        result = results_by_id.get(claim.id)
        if result is None or is_failed(result):
            continue

        if not claim.metric:
            continue

        referenced = [
            evidence_by_id[eid] for eid in claim.evidence_ids if eid in evidence_by_id
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

        metric = claim.metric.lower()
        if any(metric in e.sql.lower() for e in referenced if e.sql):
            logger.info(f"Metric {claim.metric!r} verified for claim {claim.id}")
            pass_check(result, "metric")
            continue

        fail(
            result,
            check="metric",
            reason=(
                f"Metric {claim.metric!r} not found in SQL of evidence "
                f"{[e.id for e in referenced]} for claim {claim.id}"
            ),
        )

    return verified
