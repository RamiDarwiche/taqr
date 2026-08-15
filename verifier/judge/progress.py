from __future__ import annotations

from typing import Any

PHASE_COPY: dict[str, tuple[str, str]] = {
    "list_tables": (
        "Exploring the catalog",
        "Listing tables available in the database",
    ),
    "get_schemas": (
        "Reading table schemas",
        "Loading column definitions for every table",
    ),
    "present_plan": (
        "Reviewing planner claims",
        "Loading claims and evidence to evaluate independently",
    ),
    "investigate": (
        "Checking claims",
        "Drafting an independent query to test a planner claim",
    ),
    "run_query": (
        "Running SQL",
        "Executing an independent check against the database",
    ),
    "emit_verdict": (
        "Forming a verdict",
        "Scoring semantic correctness of the planner claims",
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
    if node_name == "emit_verdict":
        return _verdict_detail(node_update)
    if node_name in {"present_plan", "get_schemas"}:
        return None

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
            if isinstance(args, dict) and (query := args.get("query")):
                return _truncate(str(query), 160)
            if name:
                return str(name)

        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return _truncate(content.strip(), 160)
        if getattr(message, "type", None) == "tool":
            text = str(content or "")
            if text:
                return _truncate(text, 160)

    return None


def _verdict_detail(node_update: dict[str, Any]) -> str | None:
    verdict = node_update.get("verdict")
    if verdict is None:
        return None
    if isinstance(verdict, dict):
        score = verdict.get("score")
        assessments = verdict.get("claim_assessments") or []
    else:
        score = getattr(verdict, "score", None)
        assessments = getattr(verdict, "claim_assessments", None) or []
    if score is None:
        return None
    score_label = score.value if hasattr(score, "value") else str(score)
    return f"{score_label} · {len(assessments)} claims assessed"


def _truncate(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"
