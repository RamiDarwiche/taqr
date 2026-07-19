from __future__ import annotations

from typing import Any
from collections.abc import Sequence

from sqlalchemy import Engine, text

from domain_types import ClaimType, VerificationStatus
from logger import logger
from planner.schemas import Claim, Evidence, PlanAgentOutput
from provenance import QueryLog
from verifier.schemas import ClaimVerification, VerifiedResponse
from verifier.verifier import AbstractVerifier, VerifierCheck


class TopKRankingVerifier(AbstractVerifier):
    def __init__(
        self,
        response: VerifiedResponse,
        engine: Engine,
        query_log: QueryLog,
        session_id: str,
        run_id: str,
    ) -> None:
        super().__init__(response, engine, query_log, session_id, run_id)
        self._replayed_rows: dict[str, list[list[Any]]] = {}

    @property
    def checks(self) -> Sequence[VerifierCheck]:
        return (self._check_top_k_row_count, self._check_top_k_subjects)

    def _rows_for(self, evidence: Evidence, engine: Engine) -> list[list[Any]]:
        if evidence.id not in self._replayed_rows:
            if not evidence.sql:
                raise ValueError(f"Evidence {evidence.id} has no SQL")
            with engine.connect() as conn:
                rows = [
                    list(row) for row in conn.execute(text(evidence.sql)).fetchall()
                ]
            logger.trace(f"SQL replay rows for evidence {evidence.id}:\n{rows}")
            self._replayed_rows[evidence.id] = rows
        return self._replayed_rows[evidence.id]

    @staticmethod
    def _add_check(result: ClaimVerification, check: str) -> None:
        if check not in result.checks:
            result.checks.append(check)

    @staticmethod
    def _fail(result: ClaimVerification, check: str, reason: str) -> None:
        TopKRankingVerifier._add_check(result, check)
        result.status = VerificationStatus.FAILED
        result.failure_reason = reason

    @staticmethod
    def _claim_contexts(
        response: VerifiedResponse,
    ) -> list[tuple[Claim, ClaimVerification, list[Evidence]]]:
        results_by_id = {result.claim_id: result for result in response.claim_results}
        evidence_by_id = {
            evidence.id: evidence for evidence in response.response.evidence
        }
        contexts: list[tuple[Claim, ClaimVerification, list[Evidence]]] = []
        for claim in response.response.claims:
            if claim.claim_type != ClaimType.RANKING_TOP_K:
                continue
            result = results_by_id.get(claim.id)
            if result is None:
                logger.error(f"No verification result exists for claim {claim.id}")
                continue
            referenced = [
                evidence_by_id[evidence_id]
                for evidence_id in claim.evidence_ids
                if evidence_id in evidence_by_id
            ]
            contexts.append((claim, result, referenced))
        return contexts

    def _check_top_k_row_count(
        self,
        response: VerifiedResponse,
        engine: Engine,
        query_log: QueryLog,
        session_id: str,
        run_id: str,
    ) -> VerifiedResponse:
        del query_log, session_id, run_id
        check = "top_k_row_count"
        for claim, result, evidence_items in self._claim_contexts(response):
            if result.status == VerificationStatus.FAILED:
                continue
            if claim.k is None or claim.k <= 0:
                reason = "top-k ranking claim must have a positive k value"
                logger.error(f"Claim {claim.id}: {reason}")
                self._fail(result, check, reason)
                continue
            if not evidence_items:
                reason = "top-k ranking claim has no valid referenced evidence"
                logger.error(f"Claim {claim.id}: {reason}")
                self._fail(result, check, reason)
                continue

            for evidence in evidence_items:
                rows = self._rows_for(evidence, engine)
                actual = len(rows)
                if actual < claim.k:
                    reason = f"Expected {claim.k} rows, got {actual}"
                    logger.error(f"{reason} for evidence {evidence.id}")
                    self._fail(result, check, reason)
                    break
                if actual > claim.k:
                    note = f"{check} expected {claim.k} rows, got {actual}"
                    logger.warning(f"{note} for evidence {evidence.id}")
                    result.status = VerificationStatus.PARTIALLY_VERIFIED
                    if note not in result.fragility_notes:
                        result.fragility_notes.append(note)
            self._add_check(result, check)
        return response

    def _check_top_k_subjects(
        self,
        response: VerifiedResponse,
        engine: Engine,
        query_log: QueryLog,
        session_id: str,
        run_id: str,
    ) -> VerifiedResponse:
        del query_log, session_id, run_id
        check = "top_k_subject"
        for claim, result, evidence_items in self._claim_contexts(response):
            if result.status == VerificationStatus.FAILED:
                continue
            subjects = (
                claim.subject if isinstance(claim.subject, list) else [claim.subject]
            )
            subjects = [subject for subject in subjects if subject is not None]
            if not subjects:
                reason = "top-k ranking claim has no subject"
                logger.error(f"Claim {claim.id}: {reason}")
                self._fail(result, check, reason)
                continue

            for evidence in evidence_items:
                rows = self._rows_for(evidence, engine)
                missing_subjects = [
                    subject
                    for subject in subjects
                    if not any(subject == value for row in rows for value in row)
                ]
                if missing_subjects:
                    reason = (
                        f"Subjects not found in replayed rows: {missing_subjects!r}"
                    )
                    logger.error(
                        f"Subjects missing from evidence {evidence.id}\n"
                        f"Missing: {missing_subjects}\nRows: {rows}"
                    )
                    self._fail(result, check, reason)
                    break

            self._add_check(result, check)
            if result.status == VerificationStatus.NOT_VERIFIED:
                result.status = VerificationStatus.VERIFIED
                result.failure_reason = None
        return response


def verify_top_k_ranking(
    claim: Claim,
    evidence: list[Evidence],
    engine: Engine,
    claim_result: ClaimVerification,
) -> ClaimVerification:
    """Compatibility wrapper for callers that verify one top-k claim."""
    response = VerifiedResponse(
        response=PlanAgentOutput(claims=[claim], evidence=evidence),
        status=VerificationStatus.NOT_VERIFIED,
        claim_results=[claim_result],
    )
    TopKRankingVerifier(
        response=response,
        engine=engine,
        query_log=QueryLog(),
        session_id="",
        run_id="",
    ).verify()
    return claim_result
