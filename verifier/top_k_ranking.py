from __future__ import annotations

from typing import Any
from sqlalchemy import Engine, text

from domain_types import VerificationStatus
from logger import logger
from planner.schemas import Claim, Evidence
from verifier.schemas import ClaimVerification


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
        with engine.connect() as conn:
            rows = [list(row) for row in conn.execute(text(e.sql)).fetchall()]
            logger.trace(f"SQL replay rows:\n{rows}")

            claim_result = _check_top_k_row_count(k, rows, e, claim_result)
            if claim_result.status == VerificationStatus.FAILED:
                return claim_result

            claim_result = _check_top_k_subjects(claim, rows, e, claim_result)
            if claim_result.status == VerificationStatus.FAILED:
                return claim_result

    claim_result.status = VerificationStatus.VERIFIED
    claim_result.failure_reason = None
    return claim_result


def _check_top_k_row_count(
    k: int, rows: list[list[Any]], evidence: Evidence, claim_result: ClaimVerification
) -> ClaimVerification:
    if len(rows) > k:
        logger.debug(
            f"Row count mismatch for evidence {evidence.id}\nExpected: {k}\nActual: {len(rows)}"
        )
        claim_result.status = VerificationStatus.PARTIALLY_VERIFIED
        claim_result.fragility_notes.append(
            f"top_k_row_count expected {k} rows, got {len(rows)}"
        )
    else:
        logger.error(
            f"Row count mismatch for evidence {evidence.id}\nExpected: {k}\nActual: {len(rows)}"
        )
        claim_result.status = VerificationStatus.FAILED
        claim_result.failure_reason = f"Expected {k} rows, got {len(rows)}"
        return claim_result
    claim_result.checks.append("top_k_row_count")
    return claim_result


def _check_top_k_subjects(
    claim: Claim,
    rows: list[list[Any]],
    evidence: Evidence,
    claim_result: ClaimVerification,
) -> ClaimVerification:
    subjects = claim.subject if isinstance(claim.subject, list) else [claim.subject]
    missing_subjects = [
        subject
        for subject in subjects
        if not any(subject == value for row in rows for value in row)
    ]
    if missing_subjects:
        logger.error(
            f"Subjects missing from evidence {evidence.id}\nMissing: {missing_subjects}\nRows: {rows}"
        )
        claim_result.status = VerificationStatus.FAILED
        claim_result.failure_reason = (
            f"Subjects not found in replayed rows: {missing_subjects!r}"
        )
        claim_result.checks.append("top_k_subjects")
        return claim_result
    return claim_result
