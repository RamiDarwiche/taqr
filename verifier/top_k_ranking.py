from __future__ import annotations

from sqlalchemy import Engine, text

from domain_types import VerificationStatus
from logger import logger
from planner.schemas import Claim, Evidence
from verifier.schemas import ClaimVerification


def verify_top_k_ranking(
    claim: Claim,
    evidence: list[Evidence],
    engine: Engine,
    result: ClaimVerification,
) -> ClaimVerification:
    """Mutate ``result`` in place with top-k checks; return the same object."""
    k = claim.k
    if not k:
        logger.error("Top-k ranking claim has no k value")
        result.status = VerificationStatus.FAILED
        result.failure_reason = "top-k ranking claim has no k value"
        return result

    evidence_by_id = {e.id: e for e in evidence}
    for evidence_id in claim.evidence_ids:
        e = evidence_by_id.get(evidence_id)
        if e is None or not e.sql:
            logger.error(f"Missing evidence {evidence_id} for claim")
            result.status = VerificationStatus.FAILED
            result.failure_reason = f"Missing evidence {evidence_id}"
            return result
        with engine.connect() as conn:
            rows = [list(row) for row in conn.execute(text(e.sql)).fetchall()]
            logger.trace(f"SQL replay rows:\n{rows}")
        if len(rows) != k:
            logger.error(
                f"Row count mismatch for evidence {e.id}\nExpected: {k}\nActual: {len(rows)}"
            )
            result.status = VerificationStatus.FAILED
            result.failure_reason = f"Expected {k} rows, got {len(rows)}"
            return result
        result.checks.append("top_k_row_count")

        subjects = claim.subject if isinstance(claim.subject, list) else [claim.subject]
        missing_subjects = [
            subject
            for subject in subjects
            if not any(subject == value for row in rows for value in row)
        ]
        if missing_subjects:
            logger.error(
                f"Subjects missing from evidence {e.id}\n"
                f"Missing: {missing_subjects}\nRows: {rows}"
            )
            result.status = VerificationStatus.FAILED
            result.failure_reason = (
                f"Subjects not found in replayed rows: {missing_subjects!r}"
            )
            return result
        result.checks.append("top_k_subject_existence")

    result.status = VerificationStatus.VERIFIED
    result.failure_reason = None
    return result
