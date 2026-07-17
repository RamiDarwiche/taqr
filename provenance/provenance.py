from __future__ import annotations

import json
from enum import Enum
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from provenance.ddl import (
    _EVENTS_TABLE__DDL,
    _MODELS_TABLE__DDL,
    _PROVENANCE_SCHEMA,
    _RUNS_TABLE__DDL,
)
from provenance.utils import _truncate


class RunStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(Enum):
    QUERY = "query"
    TOOL_CALL = "tool_call"
    QUERY_RESPONSE = "query_response"


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
        if (
            inspector.has_table("events", schema="provenance")
            and inspector.has_table("runs", schema="provenance")
            and inspector.has_table("models", schema="provenance")
        ):
            columns = {
                col["name"]
                for col in inspector.get_columns("events", schema="provenance")
                + inspector.get_columns("runs", schema="provenance")
                + inspector.get_columns("models", schema="provenance")
            }
            expected = {
                "event_id",
                "session_id",
                "run_id",
                "event_type",
                "ts",
                "payload",
                "start_ts",
                "end_ts",
                "model_id",
                "model_name",
            }
            if expected.issubset(columns):
                needs_create = False

        if needs_create:
            with self.engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS provenance.models"))
                conn.execute(text("DROP TABLE IF EXISTS provenance.events"))
                conn.execute(text("DROP TABLE IF EXISTS provenance.runs"))
                conn.execute(text(_PROVENANCE_SCHEMA))
                conn.execute(text(_MODELS_TABLE__DDL))
                conn.execute(text(_RUNS_TABLE__DDL))
                conn.execute(text(_EVENTS_TABLE__DDL))

    def close(self) -> None:
        self.engine = None

    def log_run(
        self,
        session_id: str,
        run_id: str,
        model_id: str,
        model_name: str,
        start_ts: Any = None,
        end_ts: Any = None,
    ) -> None:
        if self.engine is None:
            raise RuntimeError("QueryLog is not connected")
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO provenance.models (model_id, model_name)
                    VALUES (:model_id, :model_name)
                    ON CONFLICT (model_id) DO NOTHING
                    """),
                {"model_id": model_id, "model_name": model_name},
            )

            conn.execute(
                text("""
                    INSERT INTO provenance.runs (session_id, run_id, model_id, start_ts, end_ts)
                    VALUES (
                        :session_id,
                        :run_id,
                        :model_id,
                        COALESCE(:start_ts, CURRENT_TIMESTAMP),
                        :end_ts
                    )
                    """),
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "model_id": model_id,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                },
            )

    def finish_run(
        self,
        run_id: str,
        status: RunStatus,
        end_ts: Any = None,
        error: str | None = None,
    ) -> None:
        if self.engine is None:
            raise RuntimeError("QueryLog is not connected")
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE provenance.runs
                    SET status = :status, end_ts = COALESCE(:end_ts, CURRENT_TIMESTAMP), error = :error
                    WHERE run_id = :run_id
                    """),
                {
                    "run_id": run_id,
                    "status": status.value,
                    "end_ts": end_ts,
                    "error": error,
                },
            )

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
                text("""
                    INSERT INTO provenance.events (session_id, run_id, event_type, payload)
                    VALUES (:session_id, :run_id, :event_type, CAST(:payload AS jsonb))
                    """),
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
