from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class JudgeScore(str, Enum):
    VERY_UNCONFIDENT = "VERY_UNCONFIDENT"
    UNCONFIDENT = "UNCONFIDENT"
    SOMEWHAT_CONFIDENT = "SOMEWHAT_CONFIDENT"
    CONFIDENT = "CONFIDENT"
    VERY_CONFIDENT = "VERY_CONFIDENT"


class ClaimAssessment(BaseModel):
    """Per-claim semantic judgement from independent database checks."""

    claim_id: str
    supported: bool
    notes: str = ""


class JudgeAgentOutput(BaseModel):
    score: JudgeScore
    reasoning: str
    claim_assessments: list[ClaimAssessment] = Field(default_factory=list)
