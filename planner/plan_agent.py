from __future__ import annotations
from logger import logger

import pathlib
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_OID, uuid4, uuid5

from langgraph.graph import END, START, MessagesState, StateGraph

from db import DB
from planner.callbacks import ProvenanceToolCallback
from planner.nodes import emit_claims, make_planner_nodes, model_name, should_continue
from planner.schemas import (
    Claim,
    Evidence,
    PlanAgentOutput,
    QueryResponsePayload,
)
from domain_types import ClaimType, EventType, RunStatus
from provenance import QueryLog, fingerprint_rows


__all__ = [
    "Claim",
    "ClaimType",
    "Evidence",
    "PlanAgentOutput",
    "PlanAgentState",
    "QueryResponsePayload",
    "agent",
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

    # better error handling, more modularity?
    # testing will probably make these more necessary
    def ask(self, question: str, session_id: str, run_id: str) -> PlanAgentOutput:
        model_id = str(uuid5(NAMESPACE_OID, model_name))
        start_ts = datetime.now(UTC)
        logging_callback = ProvenanceToolCallback(self.query_log, run_id)
        self.query_log.log_run(
            session_id=session_id,
            run_id=run_id,
            model_id=model_id,
            model_name=model_name,
            start_ts=start_ts,
        )

        response: PlanAgentOutput | None = None
        try:
            for step in self.agent.stream(
                {"messages": [{"role": "user", "content": question}]},
                config={
                    "configurable": {"session_id": session_id, "run_id": run_id},
                    "callbacks": [logging_callback],
                },
                stream_mode="values",
            ):
                logger.trace(step["messages"][-1].pretty_repr())
                if step.get("claims") is not None:
                    response = PlanAgentOutput.model_validate(
                        {
                            "claims": step["claims"],
                            "evidence": step.get("evidence") or [],
                        }
                    )

            if response is None:
                raise ValueError("Plan agent finished without emitting claims/evidence")

            for evidence in response.evidence:
                evidence.result_fingerprint = fingerprint_rows(evidence)

            payload = QueryResponsePayload(query=question, response=response)
            self.query_log.log_event(
                run_id,
                EventType.QUERY_PLAN,
                payload.model_dump(mode="json"),
            )
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

        return response
