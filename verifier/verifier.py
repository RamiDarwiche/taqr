from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence

from sqlalchemy import Engine, text

from domain_types import ClaimType, VerificationStatus
from logger import logger
from planner.schemas import Claim, Evidence, PlanAgentOutput
from provenance import QueryLog
from provenance.utils import fingerprint_rows
from verifier.schemas import ClaimVerification, VerifiedResponse

VerifierCheck = Callable[
    [VerifiedResponse, Engine, QueryLog, str, str],
    VerifiedResponse,
]


class AbstractVerifier(ABC):
    def __init__(
        self,
        response: VerifiedResponse,
        engine: Engine,
        query_log: QueryLog,
        session_id: str,
        run_id: str,
    ) -> None:
        self.response = response
        self.engine = engine
        self.query_log = query_log
        self.session_id = session_id
        self.run_id = run_id

    @property
    @abstractmethod
    def checks(self) -> Sequence[VerifierCheck]:
        """Checks to run, in order, for this verifier."""
        raise NotImplementedError

    def verify(self) -> VerifiedResponse:
        for check in self.checks:
            self.response = check(
                self.response, self.engine, self.query_log, self.session_id, self.run_id
            )
        return self.response


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

    # Verify general integrity of the response:
    # There exists claims and evidence, and for each claim, there exists at least one
    # referenced piece of evidence that exists.
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

    # Import specialized verifiers here so they can inherit AbstractVerifier
    # without creating a module-level circular import.
    from verifier.top_k_ranking import TopKRankingVerifier

    verifier_types: tuple[tuple[ClaimType, type[AbstractVerifier]], ...] = (
        (ClaimType.RANKING_TOP_K, TopKRankingVerifier),
    )
    supported_types = {claim_type for claim_type, _ in verifier_types}

    results_by_id = {r.claim_id: r for r in verified.claim_results}
    for claim in claims:
        result = results_by_id[claim.id]
        if result.status == VerificationStatus.FAILED:
            continue
        logger.info(f"Verifying claim {claim.id}: {claim.claim_text}")
        if claim.claim_type not in supported_types:
            result.status = VerificationStatus.NOT_VERIFIED
            result.failure_reason = f"No verifier for claim_type={claim.claim_type}"

    for claim_type, verifier_type in verifier_types:
        if any(
            claim.claim_type == claim_type
            and results_by_id[claim.id].status != VerificationStatus.FAILED
            for claim in claims
        ):
            verified = verifier_type(
                verified, engine, query_log, session_id, run_id
            ).verify()

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


# enforce this rather than manual updating claimverification fields in specialized verifiers?
def _update_claim_results(
    results: list[ClaimVerification],
    *,
    checks: list[str],
    status: VerificationStatus | None = None,
    failure_reason: str | None = None,
) -> None:
    for result in results:
        for check in checks:
            if check not in result.checks:
                result.checks.append(check)
        if status is not None:
            result.status = status
        if failure_reason is not None:
            result.failure_reason = failure_reason


def verify_evidence_refs(
    claims: list[Claim], evidence: list[Evidence], verified: VerifiedResponse
) -> VerifiedResponse:
    """Ensure each claim cites at least one evidence id that exists in ``evidence``.

    Updates the matching ``claim_results`` entry (success appends ``evidence_refs``;
    failure marks that claim FAILED with a reason).
    """
    evidence_ids = {e.id for e in evidence}
    results_by_id = {r.claim_id: r for r in verified.claim_results}

    for claim in claims:
        result = results_by_id.get(claim.id)
        if result is None or result.status == VerificationStatus.FAILED:
            continue

        if not claim.evidence_ids:
            reason = f"claim {claim.id} has empty evidence_ids"
            logger.error(reason)
            _update_claim_results(
                [result],
                checks=["evidence_refs"],
                status=VerificationStatus.FAILED,
                failure_reason=reason,
            )
            continue

        missing = [eid for eid in claim.evidence_ids if eid not in evidence_ids]
        if missing:
            reason = f"claim {claim.id} references unknown evidence ids: {missing}"
            logger.error(reason)
            _update_claim_results(
                [result],
                checks=["evidence_refs"],
                status=VerificationStatus.FAILED,
                failure_reason=reason,
            )
            continue

        _update_claim_results([result], checks=["evidence_refs"])

    return verified


def verify_hashes(
    evidence: list[Evidence], engine: Engine, verified: VerifiedResponse
) -> VerifiedResponse:
    """Rerun each evidence SQL and compare row fingerprints to the stored hash.

    For every evidence item, updates ``verified.claim_results`` for claims that
    reference that evidence via ``evidence_ids`` (success appends checks;
    failure marks those claims FAILED with a reason).

    :param evidence: The evidence items to verify
    :type evidence: list[Evidence]
    :param engine: The database engine to use for executing SQL queries
    :type engine: Engine
    :param verified: Accumulator for per-claim verification state
    :type verified: VerifiedResponse
    :return: The same verified response, mutated in place
    :rtype: VerifiedResponse
    """
    for e in evidence:
        referencing = _claim_results_for_evidence(verified, e.id)
        if not referencing:
            logger.error(f"Evidence {e.id} is not referenced by any claim")
            continue

        if not e.result_fingerprint or not e.sql:
            reason = f"Evidence {e.id} has no result fingerprint or SQL"
            logger.error(reason)
            logger.error(e)
            _update_claim_results(
                referencing,
                checks=["hash", "row_count"],
                status=VerificationStatus.FAILED,
                failure_reason=reason,
            )
            continue

        with engine.connect() as conn:
            result = conn.execute(text(e.sql))
            rows = [list(row) for row in result.fetchall()]

        if len(rows) != e.row_count:
            reason = (
                f"Row count mismatch for evidence {e.id}: "
                f"expected {e.row_count}, got {len(rows)}"
            )
            logger.error(reason)
            _update_claim_results(
                referencing,
                checks=["hash", "row_count"],
                status=VerificationStatus.FAILED,
                failure_reason=reason,
            )
            continue

        actual = fingerprint_rows(rows)
        if actual != e.result_fingerprint:
            reason = (
                f"Hash mismatch for evidence {e.id}: "
                f"expected {e.result_fingerprint}, got {actual}"
            )
            logger.error(reason)
            _update_claim_results(
                referencing,
                checks=["hash", "row_count"],
                status=VerificationStatus.FAILED,
                failure_reason=reason,
            )
            continue

        logger.info(f"Hash verified for evidence {e.id}")
        _update_claim_results(referencing, checks=["hash", "row_count"])

    return verified


def verify_metrics(
    claims: list[Claim], evidence: list[Evidence], verified: VerifiedResponse
) -> VerifiedResponse:
    """Verify each claim's metric appears in at least one referenced evidence SQL.

    Updates ``verified.claim_results`` for the claim under test (success appends
    the ``metric`` check; failure marks that claim FAILED with a reason).
    Claims already FAILED (e.g. from hash verification) are skipped.

    :param claims: The claims to verify
    :type claims: list[Claim]
    :param evidence: The evidence items to verify
    :type evidence: list[Evidence]
    :param verified: Accumulator for per-claim verification state
    :type verified: VerifiedResponse
    :return: The same verified response, mutated in place
    :rtype: VerifiedResponse
    """
    results_by_id = {r.claim_id: r for r in verified.claim_results}
    evidence_by_id = {e.id: e for e in evidence}

    for claim in claims:
        result = results_by_id.get(claim.id)
        if result is None or result.status == VerificationStatus.FAILED:
            continue

        if not claim.metric:
            # Metrics optional for now; nothing to check.
            continue

        referenced = [
            evidence_by_id[eid] for eid in claim.evidence_ids if eid in evidence_by_id
        ]
        if not referenced:
            reason = f"Claim {claim.id} references no known evidence for metric check"
            logger.error(reason)
            _update_claim_results(
                [result],
                checks=["metric"],
                status=VerificationStatus.FAILED,
                failure_reason=reason,
            )
            continue

        metric = claim.metric.lower()
        if any(metric in e.sql.lower() for e in referenced if e.sql):
            logger.info(f"Metric {claim.metric!r} verified for claim {claim.id}")
            _update_claim_results([result], checks=["metric"])
            continue

        reason = (
            f"Metric {claim.metric!r} not found in SQL of evidence "
            f"{[e.id for e in referenced]} for claim {claim.id}"
        )
        logger.error(reason)
        _update_claim_results(
            [result],
            checks=["metric"],
            status=VerificationStatus.FAILED,
            failure_reason=reason,
        )

    return verified
