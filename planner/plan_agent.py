from __future__ import annotations
import sys
from datetime import UTC, datetime

import pathlib
import uuid
from typing import Any

from langgraph.graph import END, START, MessagesState, StateGraph

from db import DB
from planner.callbacks import ProvenanceToolCallback
from planner.nodes import (
    call_get_schema,
    check_query,
    emit_claims,
    generate_query,
    get_schema_node,
    list_tables,
    run_query_node,
    should_continue,
    model_name,
)
from planner.schemas import Claim, ClaimType, Evidence, PlanAgentOutput
from provenance.provenance import EventType, QueryLog, RunStatus

__all__ = [
    "Claim",
    "ClaimType",
    "Evidence",
    "PlanAgentOutput",
    "PlanAgentState",
    "agent",
]


class PlanAgentState(MessagesState):
    claims: list[dict[str, Any]]
    evidence: list[dict[str, Any]]


builder = StateGraph(PlanAgentState)
builder.add_node(list_tables)
builder.add_node(call_get_schema)
builder.add_node(get_schema_node, "get_schema")
builder.add_node(generate_query)
builder.add_node(check_query)
builder.add_node(run_query_node, "run_query")
builder.add_node(emit_claims)

builder.add_edge(START, "list_tables")
builder.add_edge("list_tables", "call_get_schema")
builder.add_edge("call_get_schema", "get_schema")
builder.add_edge("get_schema", "generate_query")
builder.add_conditional_edges("generate_query", should_continue)
builder.add_edge("check_query", "run_query")
builder.add_edge("run_query", "generate_query")
builder.add_edge("emit_claims", END)

agent = builder.compile()


if __name__ == "__main__":
    # try:
    #     pathlib.Path("graph.png").write_bytes(agent.get_graph().draw_mermaid_png())
    #     print("Wrote graph.png")
    # except Exception as e:
    #     print(f"Skipped graph.png ({e})")
    #     print(agent.get_graph().draw_mermaid())

    db = DB()
    query_log = QueryLog()
    query_log.connect(db.get_engine())

    # Provenance logging information
    session_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    model_id = str(uuid.uuid5(uuid.NAMESPACE_OID, model_name))
    callback = ProvenanceToolCallback(query_log, session_id, run_id)
    start_ts = datetime.now(UTC)
    query_log.log_run(
        session_id=session_id,
        run_id=run_id,
        model_id=model_id,
        model_name=model_name,
        start_ts=start_ts,
    )

    question = (
        "Is there a chip company that made more than $150 billion in revenue in 2025?"
    )
    response = None
    try:
        for step in agent.stream(
            {"messages": [{"role": "user", "content": question}]},
            config={
                "configurable": {"session_id": session_id, "run_id": run_id},
                "callbacks": [callback],
            },
            stream_mode="values",
        ):
            step["messages"][-1].pretty_print()
            if step.get("claims") is not None:
                response = {
                    "claims": step["claims"],
                    "evidence": step.get("evidence"),
                }
            else:
                response = step["messages"][-1].content
        query_log.log_event(
            session_id,
            run_id,
            EventType.QUERY_RESPONSE,
            {
                "query": question,
                "response": response,
            },
        )
    finally:
        run_status = RunStatus.COMPLETED
        if err := sys.exception():
            run_status = RunStatus.FAILED
        query_log.finish_run(run_id, run_status, datetime.now(UTC), str(err))
        query_log.close()
        db.disconnect()
