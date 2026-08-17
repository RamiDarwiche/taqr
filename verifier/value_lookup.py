"""Semantic verifier for single-attribute lookups.

"What is Bruno Senna's Q1 time in race 354?" asserts one cell of one row. It is
neither an existence question nor an aggregate, and forcing it into either type
leaves the asserted value unverified: the other specs can only carry numbers,
and a lap time is not a number.

This verifier reads the attribute for the claimed subject out of the replayed
rows and compares it to the declared expectation.
"""

from __future__ import annotations

from domain_types import Claim, ValueLookupSpec
from verifier.base import confirm, finalize_claim, inconclusive, refute
from verifier.context import VerificationContext
from verifier.domain_common import (
    resolve_replay_column,
    resolve_spec,
    resolve_subject_match,
    subject_in_predicates,
    verify_untyped,
)
from verifier.resolve import values_equal
from verifier.schemas import ClaimVerification


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    spec = resolve_spec(
        claim,
        ValueLookupSpec,
        result,
        prefix="value_lookup",
        metric_required=False,
    )
    if spec is None:
        verify_untyped(claim, context, result, prefix="value_lookup")
        return finalize_claim(result)

    located = None
    if claim.subject is not None:
        located = resolve_subject_match(
            claim,
            context,
            result,
            check="value_lookup_subject",
            preferred_column=spec.subject_column,
        )
        if located is None and subject_in_predicates(claim, context):
            # Every returned row already belongs to the subject, so the lookup
            # reads the whole result set rather than a subject-matched slice.
            confirm(
                result,
                "value_lookup_subject",
                detail="subject is pinned by evidence predicates",
            )
        elif located is None:
            inconclusive(
                result,
                check="value_lookup_subject",
                note=(
                    f"subject {claim.subject!r} appears in neither the replayed "
                    f"rows nor the predicates of the cited evidence"
                ),
            )

    values = _candidate_values(claim, context, spec, located)
    if values is None:
        inconclusive(
            result,
            check="value_lookup_value",
            note=(
                f"no cited evidence resolves value column "
                f"{spec.value_column!r} for the claimed subject"
            ),
        )
        return finalize_claim(result)
    if not values:
        return refute(
            result,
            check="value_lookup_value",
            reason=(
                f"Evidence returns no row from which to read "
                f"{spec.value_column!r}"
            ),
        )

    distinct = _distinct(values)
    if len(distinct) > 1:
        return refute(
            result,
            check="value_lookup_value",
            reason=(
                f"Lookup of {spec.value_column!r} is ambiguous: evidence holds "
                f"{len(distinct)} differing values {distinct[:4]!r}"
            ),
        )
    actual = distinct[0]
    if not values_equal(actual, spec.expected_value):
        return refute(
            result,
            check="value_lookup_value",
            reason=(
                f"Looked-up {spec.value_column!r} is {actual!r}, not the "
                f"claimed {spec.expected_value!r}"
            ),
        )
    confirm(result, "value_lookup_value")
    return finalize_claim(result)


def _candidate_values(
    claim: Claim,
    context: VerificationContext,
    spec: ValueLookupSpec,
    located: tuple[object, object] | None,
) -> list[object] | None:
    """Values of ``spec.value_column`` in rows belonging to the subject.

    Returns ``None`` when no cited evidence exposes the value column, and an
    empty list when the column resolves but no row matches the subject.
    """
    resolved_any = False
    values: list[object] = []
    subjects = [] if claim.subject is None else _subject_texts(claim)

    for replay in context.cited(claim.evidence_ids):
        if replay.rows is None:
            continue
        match = resolve_replay_column(replay, spec.value_column)
        if match is None:
            continue
        resolved_any = True
        subject_match = None
        if located is not None and located[0] is replay:
            subject_match = located[1]
        for row in replay.rows:
            if match.index >= len(row):
                continue
            if subject_match is not None and subjects:
                rendered = subject_match.value(row)  # type: ignore[attr-defined]
                if not any(values_equal(rendered, wanted) for wanted in subjects):
                    continue
            values.append(row[match.index])
    return values if resolved_any else None


def _subject_texts(claim: Claim) -> list[object]:
    return claim.subject if isinstance(claim.subject, list) else [claim.subject]


def _distinct(values: list[object]) -> list[object]:
    distinct: list[object] = []
    for value in values:
        if not any(values_equal(value, seen) for seen in distinct):
            distinct.append(value)
    return distinct
