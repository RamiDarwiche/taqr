from sqlalchemy import text
from sqlalchemy.engine import Engine
from enum import Enum


class QueryStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"


class QueryErrorType(Enum):
    SYNTAX_ERROR = "syntax_error"
    UNKNOWN_ERROR = "unknown_error"


class QueryLog:
    def __init__(self) -> None:
        self.engine: Engine | None = None

    def connect(self, engine: Engine) -> None:
        self.engine = engine
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS provenance (
                        event_id uuid PRIMARY KEY DEFAULT uuidv7(),
                        session_id uuid NOT NULL,
                        run_id uuid NOT NULL,
                        type VARCHAR(50) NOT NULL,
                        ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        payload JSONB NOT NULL
                    )
                    """
                )
            )

    def close(self) -> None:
        self.engine = None

    def log_query(
        self, session_id: str, run_id: str, type: str, payload: dict
    ) -> None:
        if self.engine is None:
            raise RuntimeError("QueryLog is not connected")
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO provenance (query, status, error_type) VALUES (:query, :status, :error_type)"
                ),
                {
                    "query": query,
                    "status": status.value,
                    "error_type": error_type.value if error_type else None,
                },
            )
