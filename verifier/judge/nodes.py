"""LangGraph nodes for the independent semantic LLM judge."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, NotRequired

from langchain.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import MessagesState
from langgraph.prebuilt import ToolNode
from sqlalchemy.engine import Engine

from common.llm import model
from common.tools import SqlTools, make_sql_tools
from planner.types import PlanAgentOutput
from verifier.judge.types import JudgeAgentOutput

MAX_SQL_ATTEMPTS = 6

judge_system_prompt = open("verifier/judge/system_prompts/JUDGE.md").read()

JUDGE_KICKOFF = (
    "Load the database catalog and table schemas. Planner claims to evaluate "
    "independently will follow."
)

_QUERY_NUDGE = (
    "You must call sql_db_query now with a read-only PostgreSQL SELECT that "
    "independently tests a planner claim. Do not re-run the planner's SQL "
    "unchanged. Do not reply in prose."
)

_EMIT_VERDICT_PROMPT = """Using only schema context and your own sql_db_query results, emit
JudgeAgentOutput. Do not treat planner SQL or evidence.rows as ground 
truth. Every planner claim must appear in claim_assessments. If you ran 
no successful independent query, score at most UNCONFIDENT.
"""


class JudgeAgentState(MessagesState):
    """Full judge graph state. Nodes typed as MessagesState only receive messages."""

    plan: PlanAgentOutput
    verdict: NotRequired[JudgeAgentOutput | None]


@dataclass(frozen=True)
class JudgeNodes:
    """Graph nodes bound to the judge's shared SQL tools."""

    tools: SqlTools
    run_query: ToolNode

    def list_tables(self, state: MessagesState, config: RunnableConfig):
        """List database tables and record them in the message history."""
        tool_call = {
            "name": "sql_db_list_tables",
            "args": {},
            "id": str(uuid.uuid4()),
            "type": "tool_call",
        }
        tool_call_message = AIMessage(content="", tool_calls=[tool_call])
        tool_message = self.tools.list_tables.invoke(tool_call, config=config)
        # Do not insert a second model turn here. Gemini requires each
        # function-call turn to follow a user turn or a function response.
        return {"messages": [tool_call_message, tool_message]}

    def get_schemas(self, state: MessagesState, config: RunnableConfig):
        """Load CREATE TABLE DDL for every non-provenance table."""
        tool_call = {
            "name": "sql_db_list_schemas",
            "args": {},
            "id": str(uuid.uuid4()),
            "type": "tool_call",
        }
        tool_call_message = AIMessage(content="", tool_calls=[tool_call])
        tool_message = self.tools.list_schemas.invoke(tool_call, config=config)
        return {"messages": [tool_call_message, tool_message]}

    def investigate(self, state: MessagesState, config: RunnableConfig):
        """Ask the model for the next independent ``sql_db_query`` check."""
        if _sql_attempt_count(state) >= MAX_SQL_ATTEMPTS:
            return {}

        system_message = {
            "role": "system",
            "content": judge_system_prompt,
        }
        messages = [system_message] + list(state["messages"])
        llm_with_tools = model.bind_tools([self.tools.query])
        response = llm_with_tools.invoke(messages, config=config)

        if not _has_run_query(state) and not response.tool_calls:
            response = llm_with_tools.invoke(
                messages + [{"role": "user", "content": _QUERY_NUDGE}],
                config=config,
            )

        if response.tool_calls:
            return {"messages": [response]}
        return {}


def make_judge_nodes(engine: Engine) -> JudgeNodes:
    """Build judge nodes whose SQL tools share ``engine``."""
    tools = make_sql_tools(engine)
    return JudgeNodes(
        tools=tools,
        run_query=ToolNode([tools.query], name="run_query"),
    )


def present_plan(state: JudgeAgentState, config: RunnableConfig):
    """Append the planner claims/evidence after schema context is in history."""
    plan = state.get("plan")
    if plan is None:
        configurable = (config or {}).get("configurable") or {}
        plan = configurable.get("plan")
    if plan is None:
        raise ValueError("Judge graph is missing planner output to evaluate")
    if not isinstance(plan, PlanAgentOutput):
        plan = PlanAgentOutput.model_validate(plan)
    return {
        "messages": [
            {"role": "user", "content": _plan_briefing(plan)},
        ]
    }


def emit_verdict(state: MessagesState, config: RunnableConfig):
    """Produce a structured semantic judgement from independent query results."""
    system_message = {
        "role": "system",
        "content": judge_system_prompt,
    }
    structured = model.with_structured_output(JudgeAgentOutput)
    result = structured.invoke(
        [system_message]
        + list(state["messages"])
        + [{"role": "user", "content": _EMIT_VERDICT_PROMPT}],
        config=config,
    )
    if not isinstance(result, JudgeAgentOutput):
        result = JudgeAgentOutput.model_validate(result)

    return {
        "messages": [AIMessage(content=result.model_dump_json())],
        "verdict": result,
    }


def should_continue(state: MessagesState) -> Literal["run_query", "emit_verdict"]:
    """Route after :func:`JudgeNodes.investigate`.

    1. Max independent query attempts → ``emit_verdict``.
    2. Pending tool calls → ``run_query``.
    3. Otherwise → ``emit_verdict`` (including the no-query case, which the
       prompt requires to be scored at most ``UNCONFIDENT``).
    """
    if _sql_attempt_count(state) >= MAX_SQL_ATTEMPTS:
        return "emit_verdict"
    messages = state.get("messages") or []
    if not messages:
        return "emit_verdict"
    last_message = messages[-1]
    if getattr(last_message, "tool_calls", None):
        return "run_query"
    return "emit_verdict"


def _plan_briefing(plan: PlanAgentOutput) -> str:
    return (
        "Independently evaluate the following planner claims and evidence for "
        "semantic correctness against the live database. Schema context is "
        "already in this conversation. Do not treat planner SQL or rows as "
        "ground truth.\n\n"
        f"{plan.model_dump_json(indent=2)}"
    )


def _has_run_query(state: MessagesState) -> bool:
    return _sql_attempt_count(state) > 0


def _sql_attempt_count(state: MessagesState) -> int:
    return sum(
        1
        for m in state["messages"]
        if isinstance(m, ToolMessage) and m.name == "sql_db_query"
    )
