from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from psycopg.errors import SyntaxError as PsycopgSyntaxError
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from db import DB
from planner.plan_agent import PlanAgent
from provenance.provenance import EventType, QueryLog
from samples import download_datasets

# is this proper fastapi?
query_log = QueryLog()
db = DB()
plan_agent = PlanAgent(db, query_log)


@asynccontextmanager
async def lifespan(app: FastAPI):
    download_datasets(db.get_engine())
    query_log.connect(db.get_engine())
    yield
    query_log.close()
    db.disconnect()


app = FastAPI(lifespan=lifespan)


class Question(BaseModel):
    question: str


@app.get("/tables")
def show_tables():
    return inspect(DB().get_engine()).get_table_names()


@app.post("/ask")
def ask(
    question: Question,
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
):
    try:
        session_id = x_session_id or str(uuid.uuid4())
        return plan_agent.ask(question.question, session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
