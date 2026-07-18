from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from domain_types import EventType, RunStatus
from provenance.ddl import (
    _EVENTS_TABLE__DDL,
    _MODELS_TABLE__DDL,
    _PROVENANCE_SCHEMA,
    _RUNS_TABLE__DDL,
)
from provenance.utils import _truncate


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


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

    def list_run_summaries(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        if self.engine is None:
            raise RuntimeError("QueryLog is not connected")
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT r.run_id, r.session_id, r.status, r.error,
                           r.start_ts, r.end_ts, m.model_name,
                           plan.payload ->> 'query' AS question,
                           verification.payload ->> 'status' AS verification_status
                    FROM provenance.runs AS r
                    JOIN provenance.models AS m ON m.model_id = r.model_id
                    LEFT JOIN LATERAL (
                        SELECT payload
                        FROM provenance.events
                        WHERE run_id = r.run_id AND event_type = 'QUERY_PLAN'
                        ORDER BY ts DESC
                        LIMIT 1
                    ) AS plan ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT payload
                        FROM provenance.events
                        WHERE run_id = r.run_id
                          AND event_type = 'QUERY_VERIFICATION'
                        ORDER BY ts DESC
                        LIMIT 1
                    ) AS verification ON TRUE
                    ORDER BY r.start_ts DESC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"limit": limit, "offset": offset},
            )
            return [dict(row) for row in rows.mappings()]

    def get_run_metadata(self, run_id: str) -> dict[str, Any] | None:
        if self.engine is None:
            raise RuntimeError("QueryLog is not connected")
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        """
                        SELECT r.run_id, r.session_id, r.status, r.error,
                               r.start_ts, r.end_ts, r.model_id, m.model_name
                        FROM provenance.runs AS r
                        JOIN provenance.models AS m ON m.model_id = r.model_id
                        WHERE r.run_id = :run_id
                        """
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .first()
            )
            return dict(row) if row is not None else None

    def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        if self.engine is None:
            raise RuntimeError("QueryLog is not connected")
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT event_id, run_id, event_type, ts, payload
                    FROM provenance.events
                    WHERE run_id = :run_id
                    ORDER BY ts, event_id
                    """
                ),
                {"run_id": run_id},
            )
            return [dict(row) for row in rows.mappings()]

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
                text(
                    """
                    INSERT INTO provenance.models (model_id, model_name)
                    VALUES (:model_id, :model_name)
                    ON CONFLICT (model_id) DO NOTHING
                    """
                ),
                {"model_id": model_id, "model_name": model_name},
            )

            conn.execute(
                text(
                    """
                    INSERT INTO provenance.runs (session_id, run_id, model_id, start_ts, end_ts)
                    VALUES (
                        :session_id,
                        :run_id,
                        :model_id,
                        COALESCE(:start_ts, CURRENT_TIMESTAMP),
                        :end_ts
                    )
                    """
                ),
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
                text(
                    """
                    UPDATE provenance.runs
                    SET status = :status, end_ts = COALESCE(:end_ts, CURRENT_TIMESTAMP), error = :error
                    WHERE run_id = :run_id
                    """
                ),
                {
                    "run_id": run_id,
                    "status": status.value,
                    "end_ts": end_ts,
                    "error": error,
                },
            )

    def log_event(
        self,
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
                    INSERT INTO provenance.events (run_id, event_type, payload)
                    VALUES (:run_id, :event_type, CAST(:payload AS jsonb))
                    """
                ),
                {
                    "run_id": run_id,
                    "event_type": event_type.value,
                    "payload": json.dumps(payload or {}, default=_json_default),
                },
            )

    def log_tool_call(
        self,
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
        self.log_event(run_id, EventType.TOOL_CALL, payload)
