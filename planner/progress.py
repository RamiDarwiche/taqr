from __future__ import annotations

from typing import Any

PHASE_COPY: dict[str, tuple[str, str]] = {
    "list_tables": (
        "Exploring the catalog",
        "Listing tables available in the database",
    ),
    "call_get_schema": (
        "Choosing relevant tables",
        "Asking the model which schemas matter for this question",
    ),
    "get_schema": (
        "Reading table schemas",
        "Fetching column definitions and sample context",
    ),
    "generate_query": (
        "Drafting SQL",
        "Planning a read-only query that can answer the question",
    ),
    "check_query": (
        "Reviewing the query",
        "Checking the candidate SQL before execution",
    ),
    "run_query": (
        "Running SQL",
        "Executing the query against the database",
    ),
    "emit_claims": (
        "Forming claims",
        "Turning query results into verifiable claims and evidence",
    ),
}


def pretty_step(node_name: str, node_update: dict[str, Any], *, index: int) -> dict[str, Any]:
    title, fallback_detail = PHASE_COPY.get(
        node_name,
        (node_name.replace("_", " ").title(), f"Completed `{node_name}`"),
    )
    detail = _extract_detail(node_name, node_update) or fallback_detail
    return {
        "id": f"{node_name}-{index}",
        "phase": node_name,
        "title": title,
        "detail": detail,
        "status": "completed",
    }


def _extract_detail(node_name: str, node_update: dict[str, Any]) -> str | None:
    if node_name == "emit_claims":
        claims = node_update.get("claims") or []
        evidence = node_update.get("evidence") or []
        return f"{len(claims)} claims · {len(evidence)} evidence items"

    messages = node_update.get("messages") or []
    if not messages:
        return None

    for message in reversed(messages):
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            call = tool_calls[0]
            if isinstance(call, dict):
                name = call.get("name")
                args = call.get("args") or {}
            else:
                name = getattr(call, "name", None)
                args = getattr(call, "args", {}) or {}
            if isinstance(args, dict):
                if query := args.get("query"):
                    return _truncate(str(query), 160)
                if tables := args.get("table_names"):
                    return _truncate(f"schema → {tables}", 160)
            if name:
                return str(name)

        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return _truncate(content.strip(), 160)
        if hasattr(message, "type") and getattr(message, "type", None) == "tool":
            text = str(content or "")
            if text:
                return _truncate(text, 160)

    return None


def _truncate(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"
