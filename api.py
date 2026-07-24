from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine, inspect, text

from benchmark.bird import (
    BirdQuestion,
    ensure_bird_dataset,
    get_question,
    get_question_by_text,
    random_question,
)
from db import DB
from domain_types import EventType
from provenance.query_log import QueryLog

ALLOWED_SCHEMAS = frozenset({"public", "provenance"})
MAX_PAGE_SIZE = 200
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class RunRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    session_id: UUID | None = None
    question_id: int | None = Field(default=None, ge=1)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _event_view(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "ts": event.get("ts"),
        "payload": event.get("payload") or {},
    }


def _normalize_run_detail(
    metadata: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    engine: Engine | None = None,
) -> dict[str, Any]:
    plan_payload: dict[str, Any] = {}
    verification: dict[str, Any] | None = None
    tool_events: list[dict[str, Any]] = []

    for event in events:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        if event_type == EventType.QUERY_PLAN.value:
            plan_payload = payload
        elif event_type == EventType.QUERY_VERIFICATION.value:
            verification = payload
        elif event_type == EventType.TOOL_CALL.value:
            tool_events.append(_event_view(event))

    plan = plan_payload.get("response") or {}
    tool_calls = [
        {
            "id": event["event_id"],
            "started_at": event["ts"],
            **event["payload"],
        }
        for event in tool_events
    ]

    benchmark: dict[str, Any] | None = None
    if isinstance(verification, dict):
        raw_benchmark = verification.get("benchmark")
        if isinstance(raw_benchmark, dict):
            benchmark = dict(raw_benchmark)
        # Keep verification payload focused on claim results for the UI.
        verification = {
            key: value
            for key, value in verification.items()
            if key != "benchmark"
        }
    if not benchmark:
        benchmark = _resolve_benchmark(
            question_id=plan_payload.get("question_id"),
            question=plan_payload.get("query"),
        )

    gold_result = None
    if benchmark is not None:
        gold_result = benchmark.get("result")
        if gold_result is None and engine is not None and benchmark.get("gold_sql"):
            gold_result = _execute_gold_sql(engine, str(benchmark["gold_sql"]))
            benchmark["result"] = gold_result

    return _json_safe(
        {
            **metadata,
            "id": metadata.get("run_id"),
            "created_at": metadata.get("start_ts"),
            "updated_at": metadata.get("end_ts"),
            "question": plan_payload.get("query"),
            "question_id": (benchmark or {}).get("question_id"),
            "db_id": (benchmark or {}).get("db_id"),
            "difficulty": (benchmark or {}).get("difficulty"),
            "gold_sql": (benchmark or {}).get("gold_sql"),
            "gold_result": gold_result,
            "plan": plan or None,
            "claims": plan.get("claims", []),
            "evidence": plan.get("evidence", []),
            "verification": verification,
            "tool_calls": tool_calls,
            "tool_events": tool_events,
        }
    )


def _benchmark_payload(item: BirdQuestion) -> dict[str, object]:
    return {
        "question_id": item.question_id,
        "db_id": item.db_id,
        "difficulty": item.difficulty,
        "gold_sql": item.gold_sql,
        "evidence": item.evidence,
    }


def _resolve_benchmark(
    *,
    question_id: int | None = None,
    question: str | None = None,
) -> dict[str, object] | None:
    item: BirdQuestion | None = None
    if question_id is not None:
        item = get_question(question_id)
    if item is None and question:
        item = get_question_by_text(question)
    return _benchmark_payload(item) if item else None


def _execute_gold_sql(engine: Engine, sql: str) -> dict[str, Any]:
    """Run a MiniDev gold query and return a UI-friendly result payload."""
    statement = sql.strip().rstrip(";")
    if not statement or not statement.lstrip().lower().startswith("select"):
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": "Gold SQL must be a single SELECT statement",
        }

    try:
        with engine.connect() as conn:
            result = conn.execute(text(statement))
            columns = list(result.keys())
            rows = [list(row) for row in result.fetchall()]
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "error": None,
        }
    except Exception as exc:
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": str(exc),
        }


def _normalize_run_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return _json_safe(
        {
            **summary,
            "id": summary.get("run_id"),
            "created_at": summary.get("start_ts"),
            "updated_at": summary.get("end_ts"),
        }
    )


def _validated_table(request: Request, schema: str, table: str) -> tuple[str, str]:
    if schema not in ALLOWED_SCHEMAS:
        raise HTTPException(status_code=404, detail="Schema not found")
    if not _IDENTIFIER.fullmatch(schema) or not _IDENTIFIER.fullmatch(table):
        raise HTTPException(status_code=400, detail="Invalid schema or table identifier")

    engine = request.app.state.db.get_engine()
    if table not in inspect(engine).get_table_names(schema=schema):
        raise HTTPException(status_code=404, detail="Table not found")

    quote = engine.dialect.identifier_preparer.quote_identifier
    return quote(schema), quote(table)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from planner.plan_agent import PlanAgent

    db = DB()
    engine = db.get_engine()
    ensure_bird_dataset(engine)
    query_log = QueryLog()
    query_log.connect(engine)
    app.state.db = db
    app.state.query_log = query_log
    app.state.plan_agent = PlanAgent(db, query_log)
    try:
        yield
    finally:
        query_log.close()
        db.disconnect()


def create_app(
    lifespan_context: Callable[[FastAPI], Any] = lifespan,
) -> FastAPI:
    app = FastAPI(title="TAQR API", lifespan=lifespan_context)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/runs")
    def create_run(
        body: RunRequest,
        request: Request,
        x_session_id: UUID | None = Header(default=None, alias="X-Session-Id"),
    ) -> JSONResponse:
        from verifier.verifier import verify_response

        session_id = str(body.session_id or x_session_id or uuid4())
        run_id = str(uuid4())
        query_log: QueryLog = request.app.state.query_log
        db: DB = request.app.state.db
        benchmark = _resolve_benchmark(
            question_id=body.question_id,
            question=body.question,
        )
        if body.question_id is not None and benchmark is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown benchmark question_id={body.question_id}",
            )

        try:
            plan = request.app.state.plan_agent.ask(body.question, session_id, run_id)
            verified = verify_response(
                plan,
                db.get_engine(),
                query_log,
                session_id,
                run_id,
                query=body.question,
            )
            verification_payload = verified.model_dump(
                mode="json", include={"status", "claim_results"}
            )
            if benchmark is not None:
                gold_sql = str(benchmark.get("gold_sql") or "")
                if gold_sql:
                    benchmark = {
                        **benchmark,
                        "result": _execute_gold_sql(db.get_engine(), gold_sql),
                    }
                verification_payload["benchmark"] = benchmark
            query_log.log_event(
                run_id,
                EventType.QUERY_VERIFICATION,
                verification_payload,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        metadata = query_log.get_run_metadata(run_id)
        if metadata is None:
            raise HTTPException(status_code=500, detail="Completed run was not persisted")
        return JSONResponse(
            content=_normalize_run_detail(
                metadata,
                query_log.get_run_events(run_id),
                engine=db.get_engine(),
            )
        )

    @app.get("/api/runs")
    def list_runs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        rows = request.app.state.query_log.list_run_summaries(
            limit=limit, offset=offset
        )
        return JSONResponse(
            content=[_normalize_run_summary(row) for row in rows]
        )

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: UUID, request: Request) -> JSONResponse:
        query_log: QueryLog = request.app.state.query_log
        metadata = query_log.get_run_metadata(str(run_id))
        if metadata is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return JSONResponse(
            content=_normalize_run_detail(
                metadata,
                query_log.get_run_events(str(run_id)),
                engine=request.app.state.db.get_engine(),
            )
        )

    @app.get("/api/benchmark/random")
    def get_random_benchmark_question() -> JSONResponse:
        item = random_question()
        return JSONResponse(content=_json_safe(item.as_api_dict()))

    @app.get("/api/benchmark/questions/{question_id}")
    def get_benchmark_question(question_id: int) -> JSONResponse:
        item = get_question(question_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Question not found")
        return JSONResponse(content=_json_safe(item.as_api_dict()))

    @app.get("/api/tables")
    def list_tables(request: Request) -> JSONResponse:
        engine = request.app.state.db.get_engine()
        inspector = inspect(engine)
        catalog: list[dict[str, Any]] = []
        for schema in sorted(ALLOWED_SCHEMAS):
            for table in sorted(inspector.get_table_names(schema=schema)):
                columns = [
                    {
                        "name": column["name"],
                        "type": str(column["type"]),
                        "nullable": column.get("nullable", True),
                    }
                    for column in inspector.get_columns(table, schema=schema)
                ]
                catalog.append(
                    {
                        "schema": schema,
                        "name": table,
                        "table": table,
                        "columns": columns,
                    }
                )
        return JSONResponse(content=_json_safe(catalog))

    @app.get("/api/tables/{schema}/{table}")
    def get_table(
        schema: str,
        table: str,
        request: Request,
        limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        quoted_schema, quoted_table = _validated_table(
            request, schema, table
        )
        engine = request.app.state.db.get_engine()
        inspector = inspect(engine)
        columns = [
            {
                "name": column["name"],
                "type": str(column["type"]),
                "nullable": column.get("nullable", True),
            }
            for column in inspector.get_columns(table, schema=schema)
        ]
        qualified_name = f"{quoted_schema}.{quoted_table}"
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"SELECT * FROM {qualified_name} "
                    "LIMIT :limit OFFSET :offset"
                ),
                {"limit": limit, "offset": offset},
            )
            total = conn.execute(
                text(f"SELECT COUNT(*) FROM {qualified_name}")
            ).scalar_one()
            data = [dict(row) for row in rows.mappings()]

        return JSONResponse(
            content=_json_safe(
                {
                    "schema": schema,
                    "table": table,
                    "columns": columns,
                    "rows": data,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                }
            )
        )

    return app


app = create_app()
