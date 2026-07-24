from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from logger import logger

_MINIDEV_ROOT = (
    Path(__file__).resolve().parent.parent
    / "minidev"
    / "minidev"
    / "MINIDEV"
)
_QUESTIONS_PATH = _MINIDEV_ROOT / "mini_dev_postgresql.json"
_GOLD_PATH = _MINIDEV_ROOT / "mini_dev_postgresql_gold.sql"
_DUMP_PATH = (
    Path(__file__).resolve().parent.parent
    / "minidev"
    / "minidev"
    / "MINIDEV_postgresql"
    / "BIRD_dev.sql"
)

# Representative BIRD MiniDev tables; presence implies the dump was loaded.
_REQUIRED_TABLES = frozenset(
    {
        "customers",
        "member",
        "schools",
        "superhero",
        "account",
    }
)


@dataclass(frozen=True, slots=True)
class BirdQuestion:
    question_id: int
    db_id: str
    question: str
    evidence: str
    gold_sql: str
    difficulty: str

    def as_api_dict(self) -> dict[str, object]:
        return asdict(self)


@lru_cache(maxsize=1)
def list_questions() -> tuple[BirdQuestion, ...]:
    """Load MiniDev questions paired with gold SQL by file order."""
    raw = json.loads(_QUESTIONS_PATH.read_text(encoding="utf-8"))
    gold_lines = _GOLD_PATH.read_text(encoding="utf-8").splitlines()
    if len(raw) != len(gold_lines):
        raise ValueError(
            f"Question/gold count mismatch: {len(raw)} questions vs "
            f"{len(gold_lines)} gold lines"
        )

    questions: list[BirdQuestion] = []
    for item, gold_line in zip(raw, gold_lines, strict=True):
        sql, db_id = gold_line.rsplit("\t", 1)
        questions.append(
            BirdQuestion(
                question_id=int(item["question_id"]),
                db_id=db_id,
                question=str(item["question"]),
                evidence=str(item.get("evidence") or ""),
                gold_sql=sql,
                difficulty=str(item.get("difficulty") or "unknown"),
            )
        )
    return tuple(questions)


def get_question(question_id: int) -> BirdQuestion | None:
    for question in list_questions():
        if question.question_id == question_id:
            return question
    return None


def get_question_by_text(question: str) -> BirdQuestion | None:
    normalized = question.strip()
    for item in list_questions():
        if item.question == normalized:
            return item
    return None


def random_question(*, rng: random.Random | None = None) -> BirdQuestion:
    pool = list_questions()
    if not pool:
        raise RuntimeError("No BIRD MiniDev questions are available")
    chooser = rng.choice if rng is not None else random.choice
    return chooser(pool)


def ensure_bird_dataset(engine: Engine) -> None:
    """Verify the BIRD MiniDev PostgreSQL dump is present; do not auto-import.

    The dump is ~1GB. Import once with:

        PGPASSWORD=taqr psql -h localhost -U taqr -d BIRD \\
          -v ON_ERROR_STOP=1 -f minidev/minidev/MINIDEV_postgresql/BIRD_dev.sql
    """
    inspector = inspect(engine)
    existing = set(inspector.get_table_names(schema="public"))
    missing = sorted(_REQUIRED_TABLES - existing)
    if missing:
        dump = _DUMP_PATH if _DUMP_PATH.exists() else "(missing dump file)"
        raise RuntimeError(
            "BIRD MiniDev tables are missing from the database "
            f"(missing: {', '.join(missing)}). "
            f"Load the dump from {dump} into the BIRD database before starting."
        )

    # Sanity: at least one domain table has rows.
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar_one()
    if count == 0:
        raise RuntimeError(
            "BIRD MiniDev appears loaded but customers is empty; re-import BIRD_dev.sql"
        )
    logger.info(
        f"BIRD MiniDev ready ({len(existing)} public tables, "
        f"{len(list_questions())} benchmark questions)"
    )
