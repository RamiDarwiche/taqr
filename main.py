from __future__ import annotations

import uuid

import verifier
from benchmark.bird import random_question
from db import DB
from domain_types import EventType
from logger import logger
from planner import PlanAgent
from provenance.query_log import QueryLog
from samples import download_datasets

query_log = QueryLog()
db = DB()
download_datasets(db.get_engine())
query_log.connect(db.get_engine())
plan_agent = PlanAgent(db, query_log)

sample = random_question()
question = sample.question
run_id = str(uuid.uuid4())
session_id = str(uuid.uuid4())
plan = plan_agent.ask(question, session_id, run_id)
verified = verifier.verify_response(
    plan, db.get_engine(), query_log, session_id, run_id, query=question
)
query_log.log_event(
    run_id,
    EventType.QUERY_VERIFICATION,
    {
        **verified.model_dump(mode="json", include={"status", "claim_results"}),
        "benchmark": {
            "question_id": sample.question_id,
            "db_id": sample.db_id,
            "difficulty": sample.difficulty,
            "gold_sql": sample.gold_sql,
        },
    },
)
logger.info(verified.model_dump(mode="json"))
logger.info(f"Gold SQL for #{sample.question_id}: {sample.gold_sql}")

query_log.close()
db.disconnect()
