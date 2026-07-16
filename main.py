from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from psycopg.errors import SyntaxError as PsycopgSyntaxError
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from db import DB
from provenance.provenance import EventType, QueryLog
from samples import download_datasets

query_log = QueryLog()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = DB()
    download_datasets(db.get_engine())
    query_log.connect(db.get_engine())
    yield
    query_log.close()
    db.disconnect()


app = FastAPI(lifespan=lifespan)


class Query(BaseModel):
    query: str


@app.get("/tables")
def show_tables():
    return inspect(DB().get_engine()).get_table_names()


@app.post("/query")
def execute_query(
    query: Query,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
):
    session_id = x_session_id or str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    with DB().get_engine().connect() as conn:
        try:
            result = conn.execute(text(query.query))
            rows = [list(row) for row in result.fetchall()]
        except ProgrammingError as e:
            query_log.log_event(
                session_id,
                run_id,
                EventType.QUERY,
                {
                    "query": query.query,
                    "status": "error",
                    "error_type": "syntax_error",
                    "error": str(e.orig or e),
                },
            )
            orig = e.orig
            if isinstance(orig, PsycopgSyntaxError):
                raise HTTPException(status_code=400, detail=str(orig)) from e
            raise HTTPException(status_code=400, detail=str(orig or e)) from e
        except SQLAlchemyError as e:
            query_log.log_event(
                session_id,
                run_id,
                EventType.QUERY,
                {
                    "query": query.query,
                    "status": "error",
                    "error_type": "unknown_error",
                    "error": str(e.orig or e),
                },
            )
            raise HTTPException(status_code=500, detail=str(e.orig or e)) from e

        query_log.log_event(
            session_id,
            run_id,
            EventType.QUERY,
            {"query": query.query, "status": "success", "row_count": len(rows)},
        )
        return {"rows": rows, "session_id": session_id, "run_id": run_id}
