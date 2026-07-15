from __future__ import annotations

import json
from enum import Enum
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


class EventType(Enum):
    QUERY = "query"
    TOOL_CALL = "tool_call"


_PROVENANCE_DDL = """
CREATE TABLE provenance (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL,
    run_id uuid NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
)
"""


class QueryLog:
    def __init__(self) -> None:
        self.engine: Engine | None = None

    def connect(self, engine: Engine) -> None:
        self.engine = engine
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        assert self.engine is not None
        inspector = inspect(self.engine)
        needs_create = True
        if inspector.has_table("provenance"):
            columns = {col["name"] for col in inspector.get_columns("provenance")}
            expected = {"event_id", "session_id", "run_id", "event_type", "ts", "payload"}
            if expected.issubset(columns):
                needs_create = False

        if needs_create:
            with self.engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS provenance"))
                conn.execute(text(_PROVENANCE_DDL))

    def close(self) -> None:
        self.engine = None

    def log_event(
        self,
        session_id: str,
        run_id: str,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.engine is None:
            raise RuntimeError("QueryLog is not connected")
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO provenance (session_id, run_id, event_type, payload)
                    VALUES (:session_id, :run_id, :event_type, CAST(:payload AS jsonb))
                    """
                ),
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "event_type": event_type.value,
                    "payload": json.dumps(payload or {}),
                },
            )

    def log_tool_call(
        self,
        session_id: str,
        run_id: str,
        *,
        tool_name: str | None,
        tool_call_id: str,
        parameters: dict[str, Any] | None,
        output: Any = None,
        status: str = "ok",
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "parameters": parameters or {},
            "status": status,
            "duration_ms": duration_ms,
        }
        if status == "ok":
            payload["output"] = _truncate(output)
        if error is not None:
            payload["error"] = error
        self.log_event(session_id, run_id, EventType.TOOL_CALL, payload)


def _truncate(value: Any, limit: int = 4000) -> Any:
    if value is None:
        return None
    if hasattr(value, "content"):
        value = value.content
    text_value = value if isinstance(value, str) else str(value)
    if len(text_value) <= limit:
        return text_value
    return text_value[:limit] + f"... [truncated {len(text_value) - limit} chars]"
