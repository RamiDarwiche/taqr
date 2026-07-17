from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from domain_types import VerificationStatus
from planner.schemas import PlanAgentOutput


class ClaimVerification(BaseModel):
    claim_id: uuid.UUID
    status: VerificationStatus
    reason: str | None = None
    checks: list[str] = Field(default_factory=list)


class VerifiedResponse(BaseModel):
    query: str | None = None
    response: PlanAgentOutput
    status: VerificationStatus
    claim_results: list[ClaimVerification] = Field(default_factory=list)
