from __future__ import annotations

from enum import Enum
from pydantic import BaseModel


class JudgeScore(str, Enum):
    VERY_UNCONFIDENT = "VERY_UNCONFIDENT"
    UNCONFIDENT = "UNCONFIDENT"
    SOMEWHAT_CONFIDENT = "SOMEWHAT_CONFIDENT"
    CONFIDENT = "CONFIDENT"
    VERY_CONFIDENT = "VERY_CONFIDENT"


class JudgeAgentOutput(BaseModel):
    score: JudgeScore
    reasoning: str
