"""Semantic verifier for comparisons between two subjects."""

from __future__ import annotations

from domain_types import Claim, ComparisonSpec
from verifier.base import confirm, finalize_claim, inconclusive, refute
from verifier.context import VerificationContext
from verifier.domain_common import collect_rows, resolve_spec, verify_untyped
from verifier.resolve import subject_list, values_equal
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import (
    compare_numbers,
    derived_change,
    numbers_equal,
    reported_numbers_equal,
)


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    spec = resolve_spec(
        claim,
        ComparisonSpec,
        result,
        prefix="comparison",
    )
    if spec is None:
        verify_untyped(claim, context, result, prefix="comparison")
        return finalize_claim(result)
    if values_equal(spec.left_subject, spec.right_subject):
        return refute(
            result,
            check="comparison_subjects",
            reason="Comparison subjects must be distinct",
        )

    declared = subject_list(claim.subject)
    missing = [
        side
        for side in (spec.left_subject, spec.right_subject)
        if not any(values_equal(side, item) for item in declared)
    ]
    if missing:
        # The compared entities live in the contract; claim.subject is a label
        # for display. A mismatch is a labelling defect, not a false comparison.
        inconclusive(
            result,
            check="comparison_subjects",
            note=(
                f"claim subject {claim.subject!r} does not list compared "
                f"entities {missing!r}"
            ),
        )

    records = collect_rows(
        claim,
        context,
        [spec.subject_column, spec.value_column],
        result,
        check="comparison_columns",
    )
    if records is None:
        return finalize_claim(result)

    left_values = _values_for(records, spec.subject_column, spec.value_column, spec.left_subject)
    right_values = _values_for(
        records, spec.subject_column, spec.value_column, spec.right_subject
    )
    if len(left_values) != 1 or len(right_values) != 1:
        return refute(
            result,
            check="comparison_subjects",
            reason=(
                "Each comparison subject must resolve to exactly one value; "
                f"got {len(left_values)} and {len(right_values)}"
            ),
        )
    confirm(result, "comparison_subjects")

    left = left_values[0]
    right = right_values[0]
    if not numbers_equal(left, spec.expected_left_value) or not numbers_equal(
        right, spec.expected_right_value
    ):
        return refute(
            result,
            check="comparison_values",
            reason=(
                f"Replayed comparison operands {left!r} and {right!r} do not "
                f"match expected {spec.expected_left_value!r} and "
                f"{spec.expected_right_value!r}"
            ),
        )
    confirm(result, "comparison_values")

    relation = compare_numbers(left, spec.operator, right)
    if relation is not True:
        return refute(
            result,
            check="comparison_relation",
            reason=f"Declared relation {spec.operator} does not hold",
        )
    confirm(result, "comparison_relation")

    if spec.delta_mode is not None and spec.expected_delta is not None:
        delta = derived_change(right, left, spec.delta_mode)
        if delta is None:
            return refute(
                result,
                check="comparison_delta",
                reason="Comparison delta is undefined (non-numeric or zero baseline)",
            )
        if not reported_numbers_equal(delta, spec.expected_delta):
            return refute(
                result,
                check="comparison_delta",
                reason=(
                    f"Computed {spec.delta_mode} delta {delta} does not match "
                    f"{spec.expected_delta}"
                ),
            )
        confirm(result, "comparison_delta")

    return finalize_claim(result)


def _values_for(
    records: list[dict[str, object]],
    subject_column: str,
    value_column: str,
    subject: str,
) -> list[object]:
    """Values whose subject cell matches ``subject`` by canonical form."""
    return [
        row[value_column]
        for row in records
        if values_equal(row[subject_column], subject)
    ]
