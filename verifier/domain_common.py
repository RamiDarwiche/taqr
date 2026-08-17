"""Small shared helpers for typed domain verifiers."""

from __future__ import annotations

from typing import Any, TypeVar

from domain_types import Claim
from verifier.base import confirm, inconclusive, refute
from verifier.context import EvidenceReplay, VerificationContext
from verifier.resolve import (
    ColumnMatch,
    SubjectMatch,
    evidence_columns,
    resolve_column,
    resolve_subject,
    subject_in_literals,
)
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import column_index, predicate_bindings, projection_names

SpecT = TypeVar("SpecT")

__all__ = [
    "collect_rows",
    "column_index",
    "replay_columns",
    "resolve_replay_column",
    "resolve_spec",
    "resolve_subject_match",
    "subject_in_predicates",
    "verify_untyped",
]


def subject_in_predicates(claim: Claim, context: VerificationContext) -> bool:
    """Whether the claim's subject is pinned by a predicate in cited SQL."""
    literals: list[Any] = []
    for replay in context.cited(claim.evidence_ids):
        if replay.query is None:
            continue
        for binding in predicate_bindings(replay.query):
            literals.extend(binding.values)
    return subject_in_literals(claim.subject, literals)


def replay_columns(replay: EvidenceReplay) -> list[str]:
    """Authoritative column names for a replay's rows."""
    aliases = projection_names(replay.query) if replay.query is not None else None
    return evidence_columns(replay.evidence, aliases=aliases)


def resolve_replay_column(replay: EvidenceReplay, name: str) -> ColumnMatch | None:
    """Resolve ``name`` against a replay's projection or its declared columns.

    Both lists index the same positions, so a spec may name either the SQL alias
    or the column the planner declared without one being wrong.
    """
    for columns in (replay_columns(replay), list(replay.evidence.columns or [])):
        if not columns:
            continue
        match = resolve_column(columns, name)
        if match is not None:
            return match
    return None


def resolve_spec(
    claim: Claim,
    expected_type: type[SpecT],
    result: ClaimVerification,
    *,
    prefix: str,
    metric_required: bool = True,
) -> SpecT | None:
    """Return the claim's typed contract, or ``None`` when it has none.

    A missing metric or a stray ``k`` is metadata noise, not a contradiction, so
    both are recorded as fragility. A missing or mismatched contract leaves the
    claim verifiable only structurally, which the caller handles via
    :func:`verify_untyped`.
    """
    if claim.k is not None and prefix != "top_k":
        inconclusive(
            result,
            check=f"{prefix}_contract",
            note=f"{prefix} claim sets k={claim.k}, which does not apply to this type",
        )
    if metric_required and not claim.metric:
        inconclusive(
            result,
            check=f"{prefix}_contract",
            note=f"{prefix} claim declares no metric to resolve against evidence",
        )
    if not isinstance(claim.verification_spec, expected_type):
        inconclusive(
            result,
            check=f"{prefix}_contract",
            note=(
                f"{prefix} claim carries no matching verification_spec; "
                f"expected values cannot be checked"
            ),
        )
        return None
    confirm(result, f"{prefix}_contract")
    return claim.verification_spec


def resolve_subject_match(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
    *,
    check: str,
    preferred_column: str | None = None,
) -> tuple[EvidenceReplay, SubjectMatch] | None:
    """Locate the claim subject in cited rows, across columns and composites.

    A subject spanning several columns — ``forename`` beside ``surname`` for
    ``"Bruno Senna"`` — resolves as a composite and counts as found. Only a match
    that needed loosening beyond normalization is reported as fragility, since
    the verifier then cannot be certain it read the subject the claim meant.
    """
    for replay in context.cited(claim.evidence_ids):
        if replay.rows is None:
            continue
        match = resolve_subject(
            claim.subject,
            replay_columns(replay),
            replay.rows,
            preferred_column=preferred_column,
        )
        if match is None:
            continue
        if match.is_approximate:
            inconclusive(
                result,
                check=check,
                note=(
                    f"subject {claim.subject!r} matches "
                    f"{match.describe()!r} in evidence {replay.evidence.id} only "
                    f"after normalization"
                ),
            )
        else:
            confirm(
                result,
                check,
                detail=f"subject resolves to {match.describe()!r}",
            )
        return replay, match
    return None


def verify_untyped(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
    *,
    prefix: str,
) -> ClaimVerification:
    """Structural verification for a claim with no usable typed contract.

    Replay integrity and filter grounding already ran in the orchestrator. What
    remains checkable without expected values is whether the claim's subject
    occurs in the evidence at all — in the replayed rows, or in the predicates
    that scope them.
    """
    if claim.subject is None:
        return result
    check = f"{prefix}_subject_grounding"
    if resolve_subject_match(claim, context, result, check=check):
        return result
    if subject_in_predicates(claim, context):
        confirm(result, check, detail="subject is pinned by evidence predicates")
        return result
    inconclusive(
        result,
        check=check,
        note=(
            f"subject {claim.subject!r} appears in neither the replayed rows nor "
            f"the predicates of the cited evidence"
        ),
    )
    return result


def collect_rows(
    claim: Claim,
    context: VerificationContext,
    columns: list[str],
    result: ClaimVerification,
    *,
    check: str,
) -> list[dict[str, Any]] | None:
    """Collect cited replay rows as records keyed by the requested columns.

    Evidence that resolves none (or only some) of the requested columns is
    skipped: claims routinely cite complementary blocks, and the orchestrator
    has already verified each block's integrity. Only when *no* cited block
    resolves the full set is the check inconclusive.
    """
    records: list[dict[str, Any]] = []
    matched_evidence = False
    approximate: list[str] = []
    partial: list[str] = []

    for replay in context.cited(claim.evidence_ids):
        if replay.error or replay.rows is None:
            refute(
                result,
                check=check,
                reason=(
                    f"Evidence {replay.evidence.id} is not replayable: {replay.error}"
                ),
            )
            return None

        matches: dict[str, ColumnMatch | None] = {
            column: resolve_replay_column(replay, column) for column in columns
        }
        unresolved = [column for column, match in matches.items() if match is None]
        if unresolved:
            if len(unresolved) < len(columns):
                partial.append(
                    f"{replay.evidence.id} resolves only "
                    f"{sorted(set(columns) - set(unresolved))}"
                )
            continue

        matched_evidence = True
        approximate.extend(
            f"{column!r}→{match.name!r} in {replay.evidence.id}"
            for column, match in matches.items()
            if match is not None and match.is_approximate
        )
        for row in replay.rows:
            if any(match.index >= len(row) for match in matches.values()):  # type: ignore[union-attr]
                continue
            records.append(
                {
                    column: row[match.index]  # type: ignore[union-attr]
                    for column, match in matches.items()
                }
            )

    if not matched_evidence:
        detail = f"; partial matches: {partial}" if partial else ""
        inconclusive(
            result,
            check=check,
            note=f"no cited evidence resolves columns {columns}{detail}",
        )
        return None
    if approximate:
        inconclusive(
            result,
            check=check,
            note=f"columns resolved only after normalization: {approximate}",
        )
    else:
        confirm(result, check)
    return records
