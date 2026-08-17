"""Shared claim-result helpers for the orchestrator and claim-type verifiers.

Type-specific modules (e.g. ``top_k_ranking``) should only implement check
logic and mutate status through these functions — never by writing
``ClaimVerification`` fields ad hoc.

Every check records a :class:`~verifier.outcome.CheckOutcome`:

* :func:`confirm` — the evidence supports the claim.
* :func:`refute` — the evidence contradicts the claim. Fails closed.
* :func:`inconclusive` — the property could not be established; the claim
  degrades to ``PARTIALLY_VERIFIED`` and the note explains the gap.
* :func:`not_applicable` — the check does not apply to this shape.

:data:`~verifier.outcome.GROUNDING_CHECKS` is enforced here rather than at each
call site: a refutation raised against a grounding check is downgraded to
inconclusive, so no naming or annotation check can hard-fail a claim.
"""

from __future__ import annotations

from collections.abc import Callable

from domain_types import VerificationStatus
from logger import logger
from verifier.outcome import CheckOutcome, CheckResult, is_grounding_check
from verifier.schemas import ClaimVerification


def is_failed(result: ClaimVerification) -> bool:
    return result.status == VerificationStatus.FAILED


def _record(
    result: ClaimVerification,
    *,
    check: str,
    outcome: CheckOutcome,
    detail: str | None = None,
) -> ClaimVerification:
    """Append a check result, keeping the legacy ``checks`` list in sync."""
    if check not in result.checks:
        result.checks.append(check)
    result.check_results.append(
        CheckResult(check=check, outcome=outcome, detail=detail)
    )
    return result


def confirm(
    result: ClaimVerification,
    check: str,
    *,
    detail: str | None = None,
) -> ClaimVerification:
    """Record a check the evidence supports, without changing status."""
    return _record(result, check=check, outcome=CheckOutcome.CONFIRMED, detail=detail)


def refute(
    result: ClaimVerification,
    *,
    check: str,
    reason: str,
) -> ClaimVerification:
    """Hard-fail a claim: the replayed evidence contradicts it.

    Grounding checks cannot refute. A refutation raised against one is recorded
    as inconclusive instead, so the policy holds even if a call site is wrong.
    """
    if is_grounding_check(check):
        return inconclusive(result, check=check, note=reason)
    logger.error(reason)
    result.status = VerificationStatus.FAILED
    result.failure_reason = reason
    return _record(result, check=check, outcome=CheckOutcome.REFUTED, detail=reason)


def inconclusive(
    result: ClaimVerification,
    *,
    check: str,
    note: str,
) -> ClaimVerification:
    """Soft-fail (outline FRAGILE): the check could not establish its property."""
    logger.debug(f"{check} inconclusive: {note}")
    if result.status != VerificationStatus.FAILED:
        result.status = VerificationStatus.PARTIALLY_VERIFIED
    result.fragility_notes.append(note)
    return _record(
        result, check=check, outcome=CheckOutcome.INCONCLUSIVE, detail=note
    )


def confirm_unless_recorded(
    result: ClaimVerification,
    check: str,
    *,
    detail: str | None = None,
) -> ClaimVerification:
    """Confirm ``check`` only if it has not already reported an outcome.

    Lets a check that emits several notes finish with a single positive record
    when none of them fired.
    """
    if any(item.check == check for item in result.check_results):
        return result
    return confirm(result, check, detail=detail)


def not_applicable(
    result: ClaimVerification,
    *,
    check: str,
    note: str | None = None,
) -> ClaimVerification:
    """Record that a check does not apply to this claim or evidence shape."""
    return _record(
        result, check=check, outcome=CheckOutcome.NOT_APPLICABLE, detail=note
    )


def run_checks(
    result: ClaimVerification,
    *steps: Callable[[], object],
) -> ClaimVerification:
    """Run check steps in order; stop at the first hard failure.

    Each step should mutate ``result`` via ``refute`` / ``inconclusive`` /
    ``confirm``. Returns ``result`` (failed or not) so callers can write::

        if is_failed(run_checks(result, step_a, step_b)):
            return result
    """
    for step in steps:
        step()
        if is_failed(result):
            break
    return result


def finalize_claim(result: ClaimVerification) -> ClaimVerification:
    """Set terminal status after a claim-type verifier finishes its checks.

    - Already ``FAILED`` → unchanged
    - Fragility notes present → ``PARTIALLY_VERIFIED``
    - Otherwise → ``VERIFIED``
    """
    if result.status == VerificationStatus.FAILED:
        return result
    if result.fragility_notes:
        result.status = VerificationStatus.PARTIALLY_VERIFIED
        return result
    result.status = VerificationStatus.VERIFIED
    result.failure_reason = None
    return result
