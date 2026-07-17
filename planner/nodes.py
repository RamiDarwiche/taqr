"""Defines langgraph nodes used in the FSM of the planner agent."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from langchain.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, MessagesState
from langgraph.prebuilt import ToolNode
from sqlalchemy.engine import Engine

from planner.llm import model
from planner.schemas import PlanAgentOutput
from planner.tools import SqlTools, make_sql_tools

model_name = getattr(model, "model_name", None) or getattr(model, "model", None)

# System prompt and constants for guiding the planner agents' reasoning.
SKIP_SCHEMA_TABLES = frozenset({"provenance"})
MAX_SQL_ATTEMPTS = 5

# Queries should be programatically enforce top_k rather than relying on the system prompt
planner_system_prompt = (
    open("planner/PLANNER_COMPRESSED.md").read().replace("{top_k}", "5")
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


@dataclass(frozen=True)
class PlannerNodes:
    """Graph nodes bound to the PlanAgent's database engine."""

    tools: SqlTools
    get_schema: ToolNode
    run_query: ToolNode

    def list_tables(self, state: MessagesState, config: RunnableConfig):
        """List database tables and record them in the message history.

        Invokes ``sql_db_list_tables``, then appends an AI summary of the form
        ``Available tables: ...`` for downstream schema selection.
        """
        tool_call = {
            "name": "sql_db_list_tables",
            "args": {},
            "id": "abc123",
            "type": "tool_call",
        }
        tool_call_message = AIMessage(content="", tool_calls=[tool_call])

        tool_message = self.tools.list_tables.invoke(tool_call, config=config)
        response = AIMessage(content=f"Available tables: {tool_message.content}")

        return {"messages": [tool_call_message, tool_message, response]}

    def call_get_schema(self, state: MessagesState, config: RunnableConfig):
        """Ask the LLM to fetch schemas, forcing a tool call if needed.

        Binds ``sql_db_schema`` and invokes the model. When the model replies
        without tool calls (common with Ollama, which ignores ``tool_choice``),
        synthesizes a forced ``sql_db_schema`` call for tables listed earlier,
        excluding :data:`SKIP_SCHEMA_TABLES`.
        """
        # tool_choice is ignored by ChatOllama — ask, then fall back to a forced call.
        llm_with_tools = model.bind_tools([self.tools.schema])
        response = llm_with_tools.invoke(state["messages"], config=config)
        if response.tool_calls:
            return {"messages": [response]}

        tables = _tables_csv_from_state(state)
        if not tables:
            return {"messages": [response]}
        return {
            "messages": [
                _forced_tool_call("sql_db_schema", {"table_names": tables}),
            ],
        }

    def generate_query(self, state: MessagesState, config: RunnableConfig):
        """Generate the next ``sql_db_query`` tool call for the user question.

        Uses the planner system prompt and binds ``sql_db_query``. On the first
        attempt, if the model answers in prose instead of calling the tool, retries
        once with a nudge. Only tool-call turns are kept; free-form answers are
        discarded so :func:`emit_claims` can enforce structured output.

        When :data:`MAX_SQL_ATTEMPTS` completed query results already exist, returns
        an empty update so routing can move to claim emission.
        """
        # Attempt budget exhausted — emit_claims will produce the structured answer.
        if _sql_attempt_count(state) >= MAX_SQL_ATTEMPTS:
            return {}

        system_message = {
            "role": "system",
            "content": planner_system_prompt,
        }
        messages = [system_message] + list(state["messages"])
        llm_with_tools = model.bind_tools([self.tools.query])
        response = llm_with_tools.invoke(messages, config=config)

        # Query nudge if the model has not yet attempted a query
        if not _has_run_query(state) and not response.tool_calls:
            response = llm_with_tools.invoke(
                messages + [{"role": "user", "content": _QUERY_NUDGE}],
                config=config,
            )

        # Only allow tool-call turns from here
        if response.tool_calls:
            return {"messages": [response]}
        return {}

    def check_query(self, state: MessagesState, config: RunnableConfig):
        """Review the pending SQL and emit (or force) a ``sql_db_query`` call.

        Reads the candidate query from the last message's tool call, asks the LLM
        (with the planner system prompt) to validate or revise it via
        ``sql_db_query``. If the model reviews in prose without a tool call,
        forces execution of the original candidate so the graph does not stall.
        The response message id is aligned with the pending tool-call message.
        """
        system_message = {
            "role": "system",
            "content": planner_system_prompt,
        }
        original = state["messages"][-1].tool_calls[0]
        candidate_query = original["args"]["query"]
        user_message = {"role": "user", "content": candidate_query}
        llm_with_tools = model.bind_tools([self.tools.query])
        response = llm_with_tools.invoke([system_message, user_message], config=config)

        # If the model reviewed in prose and skipped the tool call, execute the
        # original candidate so the graph does not stall.
        if not response.tool_calls:
            response = _forced_tool_call("sql_db_query", {"query": candidate_query})

        response.id = state["messages"][-1].id
        return {"messages": [response]}


def make_planner_nodes(engine: Engine) -> PlannerNodes:
    """Build planner nodes whose SQL tools share ``engine``."""
    tools = make_sql_tools(engine)
    return PlannerNodes(
        tools=tools,
        get_schema=ToolNode([tools.schema], name="get_schema"),
        run_query=ToolNode([tools.query], name="run_query"),
    )


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
            },
        ],
    )


def _has_run_query(state: MessagesState) -> bool:
    return _sql_attempt_count(state) > 0


def _sql_attempt_count(state: MessagesState) -> int:
    """Count completed sql_db_query tool results (authoritative attempt budget)."""
    return sum(
        1
        for m in state["messages"]
        if isinstance(m, ToolMessage) and m.name == "sql_db_query"
    )


def emit_claims(state: MessagesState, config: RunnableConfig):
    """Produce structured claims and evidence from successful query results.

    Invokes the model with structured output shaped as
    :class:`~planner.schemas.PlanAgentOutput`, instructing it to copy SQL and
    rows verbatim from prior ``sql_db_query`` tool results.

    :param state: Current planner graph state containing successful query
        tool messages to ground claims and evidence.
    :type state: MessagesState
    :param config: Runnable config forwarded to the LLM.
    :type config: RunnableConfig
    :returns: Partial state update with a JSON AI message plus typed ``claims``
        and ``evidence`` for :class:`~planner.plan_agent.PlanAgentState`.
    :rtype: dict[str, list]
    """
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
        "claims": result.claims,
        "evidence": result.evidence,
    }


def should_continue(
    state: MessagesState,
) -> Literal["check_query", "emit_claims", "__end__"]:
    """Route after :func:`generate_query` based on attempts and tool calls.

    Decision order:

    1. If completed ``sql_db_query`` results reach :data:`MAX_SQL_ATTEMPTS`,
       go to ``emit_claims``.
    2. If the last message has tool calls, go to ``check_query``.
    3. If at least one query has already run, go to ``emit_claims``.
    4. Otherwise end the graph (``END`` / ``__end__``).

    :param state: Current planner graph state after :func:`generate_query`.
    :type state: MessagesState
    :returns: Next node name — ``check_query``, ``emit_claims``, or ``__end__``.
    :rtype: Literal["check_query", "emit_claims", "__end__"]
    """
    attempts = _sql_attempt_count(state)
    if attempts >= MAX_SQL_ATTEMPTS:
        return "emit_claims"

    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "check_query"
    if attempts > 0:
        return "emit_claims"
    return END
