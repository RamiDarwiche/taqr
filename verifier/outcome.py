"""Graded outcomes for individual verifier checks.

A verifier answers two different questions, and conflating them is what makes a
trust gate untrustworthy:

* *Does the replayed evidence contradict the claim?* — a refutation. The answer
  is wrong, or the evidence no longer supports it. This must fail closed.
* *Can the verifier confirm one aspect of how the claim describes itself?* —
  grounding. Metric names, filter maps, column declarations, and SQL-shape
  conventions are annotations produced by a language model. Failing to line one
  up means the verifier could not establish that aspect, not that the answer is
  false.

Checks therefore report a :class:`CheckOutcome`. Only :attr:`CheckOutcome.REFUTED`
reaches ``FAILED``; :attr:`CheckOutcome.INCONCLUSIVE` degrades a claim to
``PARTIALLY_VERIFIED`` (the outline's FRAGILE label) and carries a note that
says what was looked for and what was found.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class CheckOutcome(str, Enum):
    """Result of a single named check."""

    CONFIRMED = "CONFIRMED"
    """The check ran and the evidence supports the claim."""

    REFUTED = "REFUTED"
    """The check ran and the evidence contradicts the claim. Fails closed."""

    INCONCLUSIVE = "INCONCLUSIVE"
    """The check ran but could not establish its property either way."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    """The check does not apply to this claim or evidence shape."""


class CheckResult(BaseModel):
    """One check, its outcome, and why."""

    check: str
    outcome: CheckOutcome
    detail: str | None = None


#: Checks whose failure means the verifier could not establish a property, and
#: which must therefore never hard-fail a claim on their own. Everything not
#: listed here is treated as refutable: a negative result is a contradiction
#: between the claim and its replayed evidence.
GROUNDING_CHECKS: frozenset[str] = frozenset(
    {
        # Orchestrator-level annotation checks.
        "columns",
        "metric",
        "filters",
        # Typed-contract presence. A missing or mismatched spec is recovered by
        # inference rather than failed.
        "aggregation_contract",
        "comparison_contract",
        "distribution_contract",
        "existence_contract",
        "trend_contract",
        "value_lookup_contract",
        # Naming and shape conventions the planner is asked to follow but whose
        # violation does not make an answer wrong.
        "top_k_filters",
        "top_k_row_count",
        "top_k_ties",
        "aggregation_scope",
        "distribution_coverage",
        "existence_subject_column",
        "value_lookup_subject",
    }
)


def is_grounding_check(check: str) -> bool:
    return check in GROUNDING_CHECKS
