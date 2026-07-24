"""Shared claim-result helpers for the orchestrator and claim-type verifiers.

Type-specific modules (e.g. ``top_k_ranking``) should only implement check
logic and mutate status through these functions — never by writing
``ClaimVerification`` fields ad hoc.
"""

from __future__ import annotations

from collections.abc import Callable

from domain_types import VerificationStatus
from logger import logger
from verifier.schemas import ClaimVerification


def is_failed(result: ClaimVerification) -> bool:
    return result.status == VerificationStatus.FAILED


def pass_check(result: ClaimVerification, check: str) -> ClaimVerification:
    """Record a successful check without changing status."""
    if check not in result.checks:
        result.checks.append(check)
    return result


def fail(
    result: ClaimVerification,
    *,
    check: str,
    reason: str,
) -> ClaimVerification:
    """Hard-fail a claim: record the check, status, and reason."""
    logger.error(reason)
    result.status = VerificationStatus.FAILED
    result.failure_reason = reason
    return pass_check(result, check)


def mark_fragile(
    result: ClaimVerification,
    *,
    check: str,
    note: str,
) -> ClaimVerification:
    """Soft-fail (outline FRAGILE): note underspecification without clearing a hard fail."""
    logger.debug(note)
    if result.status != VerificationStatus.FAILED:
        result.status = VerificationStatus.PARTIALLY_VERIFIED
    result.fragility_notes.append(note)
    return pass_check(result, check)


def run_checks(
    result: ClaimVerification,
    *steps: Callable[[], object],
) -> ClaimVerification:
    """Run check steps in order; stop at the first hard failure.

    Each step should mutate ``result`` via ``fail`` / ``mark_fragile`` /
    ``pass_check``. Returns ``result`` (failed or not) so callers can write::

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
