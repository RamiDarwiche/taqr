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
        result.reason = "top-k ranking claim has no k value"
        return result

    evidence_by_id = {e.id: e for e in evidence}
    for evidence_id in claim.evidence_ids:
        e = evidence_by_id.get(evidence_id)
        if e is None or not e.sql:
            logger.error(f"Missing evidence {evidence_id} for claim")
            result.status = VerificationStatus.FAILED
            result.reason = f"missing evidence {evidence_id}"
            return result
        with engine.connect() as conn:
            rows = [list(row) for row in conn.execute(text(e.sql)).fetchall()]
        if len(rows) != k:
            logger.error(f"Row count mismatch for evidence {e.id}")
            logger.error(f"Expected: {k}")
            logger.error(f"Actual: {len(rows)}")
            result.status = VerificationStatus.FAILED
            result.reason = f"expected {k} rows, got {len(rows)}"
            return result
        result.checks.append("top_k_row_count")
        if rows[0][0] != claim.subject:
            logger.error(f"Subject mismatch for evidence {e.id}")
            logger.error(f"Expected: {claim.subject}")
            logger.error(f"Actual: {rows[0][0]}")
            result.status = VerificationStatus.FAILED
            result.reason = (
                f"subject mismatch: expected {claim.subject!r}, got {rows[0][0]!r}"
            )
            return result
        result.checks.append("top_k_subject")

    result.status = VerificationStatus.VERIFIED
    result.reason = None
    return result
