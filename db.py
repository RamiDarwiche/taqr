import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

DEFAULT_DATABASE_URL = "postgresql+psycopg://taqr:taqr@localhost:5432/taqr"

engine: Engine | None = None


class DB:
    def __init__(self) -> None:
        self.connect()

    def get_database_url(self) -> str:
        return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

    def create_db_engine(self, url: str | None = None) -> Engine:
        return create_engine(url or self.get_database_url(), pool_pre_ping=True)

    def connect(self) -> None:
        self.engine = self.create_db_engine()
        self.engine.connect()

    def disconnect(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None

    def get_engine(self) -> Engine:
        if self.engine is None:
            raise RuntimeError("Database engine is not initialized")
        return self.engine
