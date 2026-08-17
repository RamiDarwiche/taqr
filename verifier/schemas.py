from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from domain_types import VerificationStatus
from planner.types import PlanAgentOutput
from verifier.outcome import CheckResult


class ClaimVerification(BaseModel):
    claim_id: uuid.UUID
    status: VerificationStatus
    failure_reason: str | None = None
    fragility_notes: list[str] = Field(default_factory=list)
    #: Ids of every check that ran, in order, regardless of outcome.
    checks: list[str] = Field(default_factory=list)
    #: Per-check outcome and detail. ``checks`` is the id-only projection of
    #: this list, kept for consumers that only need the names.
    check_results: list[CheckResult] = Field(default_factory=list)


class VerifiedResponse(BaseModel):
    query: str | None = None
    response: PlanAgentOutput
    status: VerificationStatus
    claim_results: list[ClaimVerification] = Field(default_factory=list)
