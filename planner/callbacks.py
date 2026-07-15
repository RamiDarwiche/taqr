from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from provenance import QueryLog


class ProvenanceToolCallback(BaseCallbackHandler):
    """Logs LangChain tool calls into the append-only provenance store."""

    def __init__(self, query_log: QueryLog, session_id: str, run_id: str) -> None:
        self.query_log = query_log
        self.session_id = session_id
        self.run_id = run_id
        self._started_at: dict[str, float] = {}
        self._tool_name: dict[str, str | None] = {}
        self._inputs: dict[str, dict[str, Any] | None] = {}

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        self._started_at[key] = time.perf_counter()
        self._tool_name[key] = serialized.get("name") or kwargs.get("name")
        self._inputs[key] = inputs

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        duration_ms = self._duration_ms(key)
        self.query_log.log_tool_call(
            self.session_id,
            self.run_id,
            tool_name=self._tool_name.pop(key, kwargs.get("name")),
            tool_call_id=key,
            parameters=self._inputs.pop(key, None),
            output=output,
            status="ok",
            duration_ms=duration_ms,
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        duration_ms = self._duration_ms(key)
        self.query_log.log_tool_call(
            self.session_id,
            self.run_id,
            tool_name=self._tool_name.pop(key, kwargs.get("name")),
            tool_call_id=key,
            parameters=self._inputs.pop(key, None),
            status="error",
            duration_ms=duration_ms,
            error=str(error),
        )

    def _duration_ms(self, key: str) -> float | None:
        started = self._started_at.pop(key, None)
        if started is None:
            return None
        return (time.perf_counter() - started) * 1000
