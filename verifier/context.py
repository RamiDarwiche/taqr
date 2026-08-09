"""Per-run verification context with one replay per evidence query."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, text
from sqlglot import exp

from planner.schemas import Evidence
from verifier.sql_analysis import parse_read_only_query


@dataclass(frozen=True)
class EvidenceReplay:
    evidence: Evidence
    query: exp.Query | None = None
    rows: list[list[object]] | None = None
    error: str | None = None


@dataclass(frozen=True)
class VerificationContext:
    engine: Engine
    evidence_by_id: dict[str, Evidence]
    replays: dict[str, EvidenceReplay]

    def cited(self, evidence_ids: list[str]) -> list[EvidenceReplay]:
        return [
            replay
            for evidence_id in evidence_ids
            if (replay := self.replays.get(evidence_id)) is not None
        ]


def build_context(
    evidence: list[Evidence],
    engine: Engine,
    *,
    referenced_ids: set[str],
) -> VerificationContext:
    """Parse and replay every referenced evidence block at most once."""
    evidence_by_id = {item.id: item for item in evidence}
    replays: dict[str, EvidenceReplay] = {}
    for item in evidence:
        if item.id not in referenced_ids or item.id in replays:
            continue
        try:
            query = parse_read_only_query(item.sql)
        except ValueError as exc:
            replays[item.id] = EvidenceReplay(item, error=str(exc))
            continue
        try:
            with engine.connect() as connection:
                rows = [
                    list(row)
                    for row in connection.execute(text(item.sql)).fetchall()
                ]
        except Exception as exc:
            replays[item.id] = EvidenceReplay(
                item,
                query=query,
                error=f"SQL replay failed: {exc}",
            )
            continue
        replays[item.id] = EvidenceReplay(item, query=query, rows=rows)
    return VerificationContext(engine, evidence_by_id, replays)
