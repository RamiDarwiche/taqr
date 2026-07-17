from enum import Enum
from planner import PlanAgentOutput, Evidence
from planner.schemas import ClaimType, Evidence, Claim
from provenance.utils import fingerprint_rows
from sqlalchemy import text, Engine
from logger import logger


class VerificationResult(Enum):
    VERIFIED = "fully_verified"
    PARTIALLY_VERIFIED = "partially_verified"
    NOT_VERIFIED = "not_verified"
    FAILED = "failed"


def verify_response(response: PlanAgentOutput, engine: Engine) -> VerificationResult:
    claims = response.claims
    evidence = response.evidence

    if not claims:
        logger.error(f"No claims were returned by the plan agent")
        return VerificationResult.FAILED
    if not evidence:
        logger.error(f"No evidence proposed by the plan agent")
        return VerificationResult.FAILED

    logger.info(f"Verifying {len(claims)} claims")
    if not verify_hash(evidence, engine):
        return VerificationResult.FAILED
    for claim in claims:
        logger.info(f"Verifying claim {claim.claim_text}")
        match claim.claim_type:
            case ClaimType.RANKING_TOP_K:
                verify_ranking_top_k(claim, evidence, engine)
            # case ClaimType.AGGREGATION:
            #     verify_aggregation(claim, evidence, engine)
            # case ClaimType.COMPARISON:
            #     verify_comparison(claim, evidence, engine)
            # case ClaimType.TREND:
            #     verify_trend(claim, evidence, engine)
            # case ClaimType.EXISTENCE:
            #     verify_existence(claim, evidence, engine)
            # case ClaimType.DISTRIBUTION:
            #     verify_distribution(claim, evidence, engine)


def verify_hash(evidence: list[Evidence], engine: Engine) -> bool:
    for e in evidence:
        if not e.result_fingerprint or not e.sql:
            logger.error(f"Evidence {e.id} has no result fingerprint or SQL")
            logger.error(e)
            return False
        with engine.connect() as conn:
            result = conn.execute(text(e.sql))
            rows = [list(row) for row in result.fetchall()]
            if len(rows) != e.row_count:
                logger.error(f"Row count mismatch for evidence {e.id}")
                logger.error(f"Expected: {e.row_count}")
                logger.error(f"Actual: {len(rows)}")
                return False
            if fingerprint_rows(rows) != e.result_fingerprint:
                logger.error(f"Hash mismatch for evidence {e.id}")
                logger.error(f"Expected: {e.result_fingerprint}")
                logger.error(f"Actual: {fingerprint_rows(rows)}")
                return False
            logger.info(f"Hash verified for evidence {e.id}")
    return True


def verify_ranking_top_k(
    claim: Claim, evidence: list[Evidence], engine: Engine
) -> bool:
    k = claim.k
    if not k:
        logger.error(f"Top-k ranking claim has no k value")
