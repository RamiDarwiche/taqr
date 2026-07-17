from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from domain_types import ClaimType


class Evidence(BaseModel):
    id: str
    sql: str
    rows: list[list[Any]]
    row_count: int
    columns: list[str]
    # null from the model; provenance fills a hex digest after the run
    result_fingerprint: str | None = None


class Claim(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
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


class QueryResponsePayload(BaseModel):
    """Provenance event payload for a completed plan-agent answer.

    Keep ``response`` as a nested object (not a JSON string) so Postgres JSONB
    stores structured claims/evidence.
    """

    query: str
    response: PlanAgentOutput
