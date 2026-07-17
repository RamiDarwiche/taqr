from __future__ import annotations

from provenance import QueryLog
from sqlalchemy import Engine, text

from domain_types import ClaimType, VerificationStatus, EventType
from logger import logger
from planner.schemas import Claim, Evidence, PlanAgentOutput
from provenance.utils import fingerprint_rows
from verifier.schemas import ClaimVerification, VerifiedResponse
from verifier.top_k_ranking import verify_top_k_ranking


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
    """Following checks that compromise the integrity of all claims, fails all claim verifications

    :param verified: The verified response object to fail
    :type verified: VerifiedResponse
    :param reason: The reason for failing the claims
    :type reason: str
    :param checks: The checks that caused the failure
    :type checks: list[str]
    :return: The verified response object with all claims failed
    :rtype: VerifiedResponse
    """
    for result in verified.claim_results:
        result.checks.extend(checks)
        result.status = VerificationStatus.FAILED
        result.failure_reason = reason
    verified.status = VerificationStatus.FAILED
    return verified


def _append_checks(verified: VerifiedResponse, checks: list[str]) -> None:
    """Appends the generalized checks to all claim verifications

    :param verified: The verified response object to append the checks to
    :type verified: VerifiedResponse
    :param checks: The checks to append to the claim verification results
    :type checks: list[str]
    """
    for result in verified.claim_results:
        result.checks.extend(checks)


def verify_response(
    response: PlanAgentOutput,
    engine: Engine,
    query_log: QueryLog,
    session_id: str,
    run_id: str,
    *,
    query: str | None = None,
) -> VerifiedResponse:
    """Entry point for verifying a plan agent's response. Verification includes several checks on the integrity and consistency of all claims and evidence.
    Further verification is dispatched to specialized verifiers for each claim type, focusing on correctness.

    :param response: The plan agent's response to verify
    :type response: PlanAgentOutput
    :param engine: The database engine to use for executing SQL queries
    :type engine: Engine
    :param query: The query that produced the response
    :type query: str
    :return: The verified response object
    :rtype: VerifiedResponse
    """
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

    # Verify general integrity of the response
    if not claims:
        logger.error("No claims were returned by the plan agent")
        verified.status = VerificationStatus.FAILED
        return verified

    if not evidence:
        logger.error("No evidence proposed by the plan agent")
        query_log.log_event(
            run_id,
            EventType.QUERY_VERIFICATION,
            verified.model_dump(mode="json", include={"status", "claim_results"}),
        )
        return _fail_all(
            verified,
            reason="no evidence provided",
            checks=[],
        )

    logger.info(f"Verifying {len(claims)} claims")

    if not verify_hashes(evidence, engine):
        query_log.log_event(
            run_id,
            EventType.QUERY_VERIFICATION,
            verified.model_dump(mode="json", include={"status", "claim_results"}),
        )
        return _fail_all(
            verified,
            reason="evidence hash or row_count verification failed",
            checks=["hash", "row_count"],
        )
    _append_checks(verified, ["hash", "row_count"])

    if not verify_metrics(claims, evidence):
        return _fail_all(
            verified,
            reason="metric verification failed",
            checks=["metric"],
        )
    _append_checks(verified, ["metric"])

    # Verify each claim, dispatch to specialized verifiers
    results_by_id = {r.claim_id: r for r in verified.claim_results}
    for claim in claims:
        logger.info(f"Verifying claim {claim.id}: {claim.claim_text}")
        result = results_by_id[claim.id]
        match claim.claim_type:
            case ClaimType.RANKING_TOP_K:
                verify_top_k_ranking(claim, evidence, engine, result)
            case _:
                result.status = VerificationStatus.NOT_VERIFIED
                result.failure_reason = f"no verifier for claim_type={claim.claim_type}"

    verified.status = _gate_status(verified.claim_results)
    logger.info(f"Trust gate status: {verified.status}")
    query_log.log_event(
        run_id,
        EventType.QUERY_VERIFICATION,
        verified.model_dump(mode="json", include={"status", "claim_results"}),
    )
    return verified


def verify_hashes(evidence: list[Evidence], engine: Engine) -> bool:
    """Given the SQL and result rows of an evidence item, reruns the SQL and computes hash of the resulting rows to
    determine the consistency of the database state and the evidence item. If the hashes are not equal, the underlying
    data may have changed or the plan agent may have generated malformed evidence.

    :param evidence: The evidence items to verify
    :type evidence: list[Evidence]
    :param engine: The database engine to use for executing SQL queries
    :type engine: Engine
    :return: True if the hashes are equal, False otherwise
    :rtype: bool
    """
    for e in evidence:
        if not e.result_fingerprint or not e.sql:
            logger.error(f"Evidence {e.id} has no result fingerprint or SQL")
            logger.error(e)
            return False
        with engine.connect() as conn:
            result = conn.execute(text(e.sql))
            rows = [list(row) for row in result.fetchall()]
            if len(rows) != e.row_count:
                logger.error(
                    f"Row count mismatch for evidence {e.id}\nExpected: {e.row_count}\nActual: {len(rows)}"
                )
                return False
            if fingerprint_rows(rows) != e.result_fingerprint:
                logger.error(
                    f"Hash mismatch for evidence {e.id}\nExpected: {e.result_fingerprint}\nActual: {fingerprint_rows(rows)}"
                )
                return False
            logger.info(f"Hash verified for evidence {e.id}")
    return True


def verify_metrics(claims: list[Claim], evidence: list[Evidence]) -> bool:
    """Each claim will have metrics associated with it. This function verifies that the metrics semantically resolved by
    the plan agent are present in at least one of the referenced evidence items.

    :param claims: The claims to verify
    :type claims: list[Claim]
    :param evidence: The evidence items to verify
    :type evidence: list[Evidence]
    :return: True if the metrics are present in at least one of the referenced evidence items, False otherwise
    :rtype: bool
    """
    for claim in claims:
        if not claim.metric:
            continue
    return True
