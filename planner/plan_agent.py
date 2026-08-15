from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_OID, uuid5

from langgraph.graph import END, START, MessagesState, StateGraph

from common.callbacks import ProvenanceToolCallback
from db import DB
from domain_types import Claim, ClaimType, EventType, Evidence, RunStatus
from logger import logger
from planner.nodes import emit_claims, make_planner_nodes, model_name, should_continue
from planner.progress import pretty_step
from planner.types import (
    PlanAgentOutput,
    QueryResponsePayload,
)
from provenance import QueryLog, attach_result_fingerprints

ProgressCallback = Callable[[dict[str, Any]], None]

__all__ = [
    "Claim",
    "ClaimType",
    "Evidence",
    "PlanAgentOutput",
    "PlanAgentState",
    "QueryResponsePayload",
]


class PlanAgentState(MessagesState):
    claims: list[Claim]
    evidence: list[Evidence]


class PlanAgent:
    def __init__(self, db: DB, query_log: QueryLog):
        self.db = db
        self.query_log = query_log
        nodes = make_planner_nodes(db.get_engine())

        self.builder = StateGraph(PlanAgentState)
        self.builder.add_node(nodes.list_tables)
        self.builder.add_node(nodes.call_get_schema)
        self.builder.add_node(nodes.get_schema, "get_schema")
        self.builder.add_node(nodes.generate_query)
        self.builder.add_node(nodes.check_query)
        self.builder.add_node(nodes.run_query, "run_query")
        self.builder.add_node(emit_claims)

        self.builder.add_edge(START, "list_tables")
        self.builder.add_edge("list_tables", "call_get_schema")
        self.builder.add_edge("call_get_schema", "get_schema")
        self.builder.add_edge("get_schema", "generate_query")
        self.builder.add_conditional_edges("generate_query", should_continue)
        self.builder.add_edge("check_query", "run_query")
        self.builder.add_edge("run_query", "generate_query")
        self.builder.add_edge("emit_claims", END)
        self.agent = self.builder.compile()

    def ask(
        self,
        question: str,
        session_id: str,
        run_id: str,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> PlanAgentOutput:
        plan: PlanAgentOutput | None = None
        for kind, payload in self.iter_ask(
            question, session_id, run_id, on_progress=on_progress
        ):
            if kind == "plan":
                plan = payload
        if plan is None:
            raise ValueError("Plan agent finished without emitting claims/evidence")
        return plan

    def iter_ask(
        self,
        question: str,
        session_id: str,
        run_id: str,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> Iterator[tuple[str, Any]]:
        """Yield ``(\"step\", event)`` updates, then ``(\"plan\", PlanAgentOutput)``."""
        model_id = str(uuid5(NAMESPACE_OID, model_name))
        start_ts = datetime.now(UTC)
        logging_callback = ProvenanceToolCallback(
            self.query_log, run_id, agent="planner"
        )
        self.query_log.log_run(
            session_id=session_id,
            run_id=run_id,
            model_id=model_id,
            model_name=model_name,
            start_ts=start_ts,
        )

        response: PlanAgentOutput | None = None
        step_index = 0
        try:
            if on_progress is not None:
                on_progress(
                    {
                        "id": "start",
                        "phase": "start",
                        "title": "Thinking",
                        "detail": "Opening an investigation over the database",
                        "status": "started",
                    }
                )

            for update in self.agent.stream(
                {"messages": [{"role": "user", "content": question}]},
                config={
                    "configurable": {"session_id": session_id, "run_id": run_id},
                    "callbacks": [logging_callback],
                },
                stream_mode="updates",
            ):
                if not isinstance(update, dict):
                    continue
                for node_name, node_update in update.items():
                    if not isinstance(node_update, dict):
                        continue
                    step_index += 1
                    event = pretty_step(node_name, node_update, index=step_index)
                    logger.trace(f"progress:{event['phase']} {event['title']}")
                    if on_progress is not None:
                        on_progress(event)
                    yield ("step", event)

                    if node_update.get("claims") is not None:
                        response = PlanAgentOutput.model_validate(
                            {
                                "claims": node_update["claims"],
                                "evidence": node_update.get("evidence") or [],
                            }
                        )

            if response is None:
                raise ValueError("Plan agent finished without emitting claims/evidence")

            # Fingerprint SQL replay (same channel as verify_hashes), not LLM-copied rows.
            attach_result_fingerprints(self.db.get_engine(), response.evidence)

            payload = QueryResponsePayload(query=question, response=response)
            self.query_log.log_event(
                run_id,
                EventType.QUERY_PLAN,
                payload.model_dump(mode="json"),
            )
            yield ("plan", response)
        finally:
            run_status = RunStatus.COMPLETED
            if err := sys.exception():
                run_status = RunStatus.FAILED
            self.query_log.finish_run(
                run_id,
                run_status,
                datetime.now(UTC),
                str(err) if err else None,
            )
