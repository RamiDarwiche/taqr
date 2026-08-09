"""Curated adversarial question bank for trust-boundary stress testing.

Unlike hard MiniDev filters, these items are hand-authored traps: complementary
evidence, limited absence proofs, under-k rankings, ties, zero baselines,
partial distributions, subject paraphrase, and claim-type boundary errors.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from benchmark.bird import BirdQuestion

_BANK_PATH = Path(__file__).resolve().parent / "adversarial_bank.json"


@dataclass(frozen=True, slots=True)
class AdversarialQuestion:
    question: BirdQuestion
    tags: tuple[str, ...]
    attack: str

    def as_api_dict(self) -> dict[str, object]:
        return {
            **self.question.as_api_dict(),
            "adversarial_tags": list(self.tags),
            "attack": self.attack,
        }


@lru_cache(maxsize=1)
def list_adversarial_questions() -> tuple[AdversarialQuestion, ...]:
    raw = json.loads(_BANK_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"Adversarial bank is empty: {_BANK_PATH}")

    bank: list[AdversarialQuestion] = []
    seen_ids: set[int] = set()
    for item in raw:
        question_id = int(item["question_id"])
        if question_id in seen_ids:
            raise ValueError(f"Duplicate adversarial question_id={question_id}")
        seen_ids.add(question_id)
        tags = tuple(str(tag) for tag in item.get("tags") or ())
        if not tags:
            raise ValueError(f"Adversarial question {question_id} has no tags")
        bank.append(
            AdversarialQuestion(
                question=BirdQuestion(
                    question_id=question_id,
                    db_id=str(item["db_id"]),
                    question=str(item["question"]),
                    evidence=str(item.get("evidence") or ""),
                    gold_sql=str(item["gold_sql"]),
                    difficulty=str(item.get("difficulty") or "adversarial"),
                ),
                tags=tags,
                attack=str(item.get("attack") or ""),
            )
        )
    return tuple(bank)


def get_adversarial_question(question_id: int) -> AdversarialQuestion | None:
    for item in list_adversarial_questions():
        if item.question.question_id == question_id:
            return item
    return None


def get_adversarial_question_by_text(question: str) -> AdversarialQuestion | None:
    normalized = question.strip()
    for item in list_adversarial_questions():
        if item.question.question == normalized:
            return item
    return None


def random_adversarial_question(
    *, rng: random.Random | None = None
) -> AdversarialQuestion:
    bank = list_adversarial_questions()
    chooser = rng.choice if rng is not None else random.choice
    return chooser(bank)
