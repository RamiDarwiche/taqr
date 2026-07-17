from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import inspect

from db import DB
from planner import PlanAgent
from provenance.query_log import QueryLog
from samples import download_datasets
from logger import logger
import verifier

# is this proper fastapi?
query_log = QueryLog()
db = DB()
query_log.connect(db.get_engine())
plan_agent = PlanAgent(db, query_log)


# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     download_datasets(db.get_engine())
#     query_log.connect(db.get_engine())
#     yield
#     query_log.close()
#     db.disconnect()


# app = FastAPI(lifespan=lifespan)


# class Question(BaseModel):
#     question: str


# @app.get("/tables")
# def show_tables():
#     return inspect(DB().get_engine()).get_table_names()


# @app.post("/ask")
# def ask(
#     question: Question,
#     x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
# ):
#     try:
#         session_id = x_session_id or str(uuid.uuid4())
#         return plan_agent.ask(question.question, session_id)
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))

question = "What is the most widely manufactured AI chip in the dataset?"
session_id = str(uuid.uuid4())
plan = plan_agent.ask(question, session_id)
verified = verifier.verify_response(plan, db.get_engine(), query=question)
logger.info(verified.model_dump(mode="json"))

query_log.close()
db.disconnect()
