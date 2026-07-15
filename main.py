from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from psycopg.errors import SyntaxError as PsycopgSyntaxError
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from db import DB
from provenance import QueryLog, QueryStatus, QueryErrorType
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
def execute_query(query: Query):
    with DB().get_engine().connect() as conn:
        try:
            result = conn.execute(text(query.query))
            rows = [list(row) for row in result.fetchall()]
        except ProgrammingError as e:
            query_log.log_query(
                query.query, QueryStatus.ERROR, QueryErrorType.SYNTAX_ERROR
            )
            orig = e.orig
            if isinstance(orig, PsycopgSyntaxError):
                raise HTTPException(status_code=400, detail=str(orig)) from e
            raise HTTPException(status_code=400, detail=str(orig or e)) from e
        except SQLAlchemyError as e:
            query_log.log_query(
                query.query, QueryStatus.ERROR, QueryErrorType.UNKNOWN_ERROR
            )
            raise HTTPException(status_code=500, detail=str(e.orig or e)) from e
        query_log.log_query(query.query, QueryStatus.SUCCESS)
        return {"rows": rows}
