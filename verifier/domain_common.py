"""Small shared helpers for typed domain verifiers."""

from __future__ import annotations

from typing import TypeVar

from planner.schemas import Claim
from verifier.base import fail, pass_check
from verifier.context import VerificationContext
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import column_index

SpecT = TypeVar("SpecT")


def require_contract(
    claim: Claim,
    expected_type: type[SpecT],
    result: ClaimVerification,
    *,
    prefix: str,
    metric_required: bool = True,
) -> SpecT | None:
    if claim.k is not None:
        fail(
            result,
            check=f"{prefix}_contract",
            reason=f"{prefix} claims must not set k",
        )
        return None
    if metric_required and not claim.metric:
        fail(
            result,
            check=f"{prefix}_contract",
            reason=f"{prefix} claims require a metric",
        )
        return None
    if not isinstance(claim.verification_spec, expected_type):
        fail(
            result,
            check=f"{prefix}_contract",
            reason=f"{prefix} claim requires a matching verification_spec",
        )
        return None
    pass_check(result, f"{prefix}_contract")
    return claim.verification_spec


def collect_rows(
    claim: Claim,
    context: VerificationContext,
    columns: list[str],
    result: ClaimVerification,
    *,
    check: str,
) -> list[dict[str, object]] | None:
    """Collect cited replay rows as alias-keyed records."""
    records: list[dict[str, object]] = []
    matched_evidence = False
    for replay in context.cited(claim.evidence_ids):
        if replay.error or replay.rows is None:
            fail(
                result,
                check=check,
                reason=f"Evidence {replay.evidence.id} is not replayable: {replay.error}",
            )
            return None
        indices = {
            column: column_index(replay.evidence, column) for column in columns
        }
        unresolved = [column for column, index in indices.items() if index is None]
        if len(unresolved) == len(columns):
            # Claims may cite complementary evidence blocks. A block that
            # contains none of this check's columns is verified for integrity
            # by the orchestrator but is not relevant to this semantic check.
            continue
        if unresolved:
            fail(
                result,
                check=check,
                reason=(
                    f"Evidence {replay.evidence.id} does not resolve columns "
                    f"{unresolved} unambiguously"
                ),
            )
            return None
        matched_evidence = True
        for row in replay.rows:
            records.append(
                {column: row[index] for column, index in indices.items() if index is not None}
            )
    if not matched_evidence:
        fail(
            result,
            check=check,
            reason=f"No cited evidence resolves required columns {columns}",
        )
        return None
    return records
