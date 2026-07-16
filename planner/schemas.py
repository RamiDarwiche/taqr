from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ClaimType(str, Enum):
    ranking_top_k = "ranking_top_k"
    aggregation = "aggregation"
    comparison = "comparison"
    trend = "trend"
    existence = "existence"
    distribution = "distribution"


class Evidence(BaseModel):
    id: str
    sql: str
    rows: list[list[Any]]
    row_count: int
    columns: list[str]
    # null from the model; provenance fills a hex digest after the run
    result_fingerprint: str | None = None


class Claim(BaseModel):
    claim_text: str
    claim_type: ClaimType
    subject: str | list[str] | None = None
    metric: str | None = None
    k: int | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str]


class PlanAgentOutput(BaseModel):
    """Mode A final output: machine-verifiable claims + supporting evidence."""

    claims: list[Claim]
    evidence: list[Evidence]
