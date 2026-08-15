from __future__ import annotations

from pydantic import BaseModel
from domain_types.claims import Claim, Evidence

__all__ = [
    "Claim",
    "Evidence",
    "PlanAgentOutput",
    "QueryResponsePayload",
]


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
