from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from common.callbacks import ProvenanceToolCallback
from db import DB
from planner.types import PlanAgentOutput
from provenance import QueryLog
from domain_types import EventType
from logger import logger
from verifier.judge.nodes import (
    JUDGE_KICKOFF,
    JudgeAgentState,
    emit_verdict,
    make_judge_nodes,
    present_plan,
    should_continue,
)
from verifier.judge.progress import pretty_step
from verifier.judge.types import JudgeAgentOutput


class JudgeAgent:
    def __init__(self, db: DB, query_log: QueryLog):
        self.db = db
        self.query_log = query_log
        nodes = make_judge_nodes(db.get_engine())

        self.builder = StateGraph(JudgeAgentState)
        self.builder.add_node(nodes.list_tables)
        self.builder.add_node(nodes.get_schemas)
        self.builder.add_node(present_plan)
        self.builder.add_node(nodes.investigate)
        self.builder.add_node(nodes.run_query, "run_query")
        self.builder.add_node(emit_verdict)

        self.builder.add_edge(START, "list_tables")
        self.builder.add_edge("list_tables", "get_schemas")
        self.builder.add_edge("get_schemas", "present_plan")
        self.builder.add_edge("present_plan", "investigate")
        self.builder.add_conditional_edges("investigate", should_continue)
        self.builder.add_edge("run_query", "investigate")
        self.builder.add_edge("emit_verdict", END)
        self.agent = self.builder.compile()

    def judge(
        self,
        plan: PlanAgentOutput,
        session_id: str,
        run_id: str,
    ) -> JudgeAgentOutput:
        logging_callback = ProvenanceToolCallback(self.query_log, run_id, agent="judge")
        verdict: JudgeAgentOutput | None = None
        step_index = 0

        for update in self.agent.stream(
            {"messages": [{"role": "user", "content": JUDGE_KICKOFF}], "plan": plan, "verdict": None},
            config={
                "configurable": {
                    "session_id": session_id,
                    "run_id": run_id,
                    "plan": plan.model_dump(mode="json"),
                },
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
                if node_update.get("verdict") is None:
                    continue
                raw = node_update["verdict"]
                verdict = (
                    raw
                    if isinstance(raw, JudgeAgentOutput)
                    else JudgeAgentOutput.model_validate(raw)
                )

        if verdict is None:
            raise ValueError("Judge agent finished without emitting a verdict")

        self.query_log.log_event(
            run_id,
            EventType.QUERY_JUDGE,
            verdict.model_dump(mode="json"),
        )
        return verdict
