from __future__ import annotations

import uuid
from typing import Literal

from langchain.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, MessagesState
from langgraph.prebuilt import ToolNode

from planner.llm import model
from planner.schemas import PlanAgentOutput
from planner.tools import (
    sql_db_list_tables,
    sql_db_query,
    sql_db_query_checker,
    sql_db_schema,
)

model_name = getattr(model, "model_name", None) or getattr(model, "model", None)

tools = [sql_db_list_tables, sql_db_schema, sql_db_query, sql_db_query_checker]

get_schema_tool = next(t for t in tools if t.name == "sql_db_schema")
get_schema_node = ToolNode([get_schema_tool], name="get_schema")

run_query_tool = next(t for t in tools if t.name == "sql_db_query")
run_query_node = ToolNode([run_query_tool], name="run_query")

SKIP_SCHEMA_TABLES = frozenset({"provenance"})
MAX_SQL_ATTEMPTS = 5

planner_system_prompt = (
    open("planner/PLANNER_COMPRESSED.md", "r").read().replace("{top_k}", "5")
)

_QUERY_NUDGE = (
    "You must call sql_db_query now with a single PostgreSQL SELECT that answers "
    "the user's question using the schema already provided. Do not reply in prose."
)

_EMIT_CLAIMS_PROMPT = (
    "Using only successful sql_db_query tool results already in this conversation, "
    "emit claims and evidence. Copy sql and rows verbatim from tool output — "
    "never invent or round values. Derive columns from SELECT aliases. "
    "Set result_fingerprint to null. Every evidence_ids entry must match an "
    "evidence.id you include."
)


def list_tables(state: MessagesState, config: RunnableConfig):
    tool_call = {
        "name": "sql_db_list_tables",
        "args": {},
        "id": "abc123",
        "type": "tool_call",
    }
    tool_call_message = AIMessage(content="", tool_calls=[tool_call])

    list_tables_tool = next(t for t in tools if t.name == "sql_db_list_tables")
    tool_message = list_tables_tool.invoke(tool_call, config=config)
    response = AIMessage(content=f"Available tables: {tool_message.content}")

    return {"messages": [tool_call_message, tool_message, response]}


def _tables_csv_from_state(state: MessagesState) -> str:
    for msg in reversed(state["messages"]):
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.startswith("Available tables:"):
            names = [
                t.strip()
                for t in content.split(":", 1)[1].split(",")
                if t.strip() and t.strip() not in SKIP_SCHEMA_TABLES
            ]
            return ", ".join(names)
    return ""


def _forced_tool_call(name: str, args: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": str(uuid.uuid4()),
                "type": "tool_call",
            }
        ],
    )


def call_get_schema(state: MessagesState, config: RunnableConfig):
    # tool_choice is ignored by ChatOllama — ask, then fall back to a forced call.
    llm_with_tools = model.bind_tools([get_schema_tool])
    response = llm_with_tools.invoke(state["messages"], config=config)
    if response.tool_calls:
        return {"messages": [response]}

    tables = _tables_csv_from_state(state)
    if not tables:
        return {"messages": [response]}
    return {
        "messages": [
            _forced_tool_call("sql_db_schema", {"table_names": tables}),
        ]
    }


def _has_run_query(state: MessagesState) -> bool:
    return _sql_attempt_count(state) > 0


def _sql_attempt_count(state: MessagesState) -> int:
    """Count completed sql_db_query tool results (authoritative attempt budget)."""
    return sum(
        1
        for m in state["messages"]
        if isinstance(m, ToolMessage) and m.name == "sql_db_query"
    )


def generate_query(state: MessagesState, config: RunnableConfig):
    # Attempt budget exhausted — emit_claims will produce the structured answer.
    if _sql_attempt_count(state) >= MAX_SQL_ATTEMPTS:
        return {}

    system_message = {
        "role": "system",
        "content": planner_system_prompt,
    }
    messages = [system_message] + list(state["messages"])
    llm_with_tools = model.bind_tools([run_query_tool])
    response = llm_with_tools.invoke(messages, config=config)

    # First pass: Ollama cannot honor tool_choice, so nudge once if the model
    # replied in prose instead of calling sql_db_query.
    if not _has_run_query(state) and not response.tool_calls:
        response = llm_with_tools.invoke(
            messages + [{"role": "user", "content": _QUERY_NUDGE}],
            config=config,
        )

    # Only keep tool-call turns here. Free-form "answers" are discarded so
    # emit_claims can enforce PlanAgentOutput instead.
    if response.tool_calls:
        return {"messages": [response]}
    return {}


def check_query(state: MessagesState, config: RunnableConfig):
    system_message = {
        "role": "system",
        "content": planner_system_prompt,
    }
    original = state["messages"][-1].tool_calls[0]
    candidate_query = original["args"]["query"]
    user_message = {"role": "user", "content": candidate_query}
    llm_with_tools = model.bind_tools([run_query_tool])
    response = llm_with_tools.invoke([system_message, user_message], config=config)

    # If the model reviewed in prose and skipped the tool call, execute the
    # original candidate so the graph does not stall.
    if not response.tool_calls:
        response = _forced_tool_call("sql_db_query", {"query": candidate_query})

    response.id = state["messages"][-1].id
    return {"messages": [response]}


def emit_claims(state: MessagesState, config: RunnableConfig):
    system_message = {
        "role": "system",
        "content": planner_system_prompt,
    }
    structured = model.with_structured_output(PlanAgentOutput)
    result = structured.invoke(
        [system_message]
        + list(state["messages"])
        + [{"role": "user", "content": _EMIT_CLAIMS_PROMPT}],
        config=config,
    )
    if not isinstance(result, PlanAgentOutput):
        result = PlanAgentOutput.model_validate(result)

    return {
        "messages": [AIMessage(content=result.model_dump_json())],
        "claims": [c.model_dump() for c in result.claims],
        "evidence": [e.model_dump() for e in result.evidence],
    }


def should_continue(
    state: MessagesState,
) -> Literal["check_query", "emit_claims", "__end__"]:
    attempts = _sql_attempt_count(state)
    if attempts >= MAX_SQL_ATTEMPTS:
        return "emit_claims"

    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "check_query"
    if attempts > 0:
        return "emit_claims"
    return END
