"""Semantic verifier for comparisons between two subjects."""

from __future__ import annotations

from planner.types import Claim, ComparisonSpec
from verifier.base import fail, finalize_claim, pass_check
from verifier.context import VerificationContext
from verifier.domain_common import collect_rows, require_contract
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import (
    compare_numbers,
    derived_change,
    numbers_equal,
)


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    spec = require_contract(
        claim,
        ComparisonSpec,
        result,
        prefix="comparison",
    )
    if spec is None:
        return result
    if spec.left_subject == spec.right_subject:
        return fail(
            result,
            check="comparison_subjects",
            reason="Comparison subjects must be distinct",
        )
    if not isinstance(claim.subject, list) or not {
        spec.left_subject,
        spec.right_subject,
    }.issubset(set(claim.subject)):
        return fail(
            result,
            check="comparison_subjects",
            reason="Claim subject must include both comparison subjects",
        )

    records = collect_rows(
        claim,
        context,
        [spec.subject_column, spec.value_column],
        result,
        check="comparison_columns",
    )
    if records is None:
        return result
    by_subject: dict[str, list[object]] = {}
    for row in records:
        by_subject.setdefault(str(row[spec.subject_column]), []).append(
            row[spec.value_column]
        )
    left_values = by_subject.get(spec.left_subject, [])
    right_values = by_subject.get(spec.right_subject, [])
    if len(left_values) != 1 or len(right_values) != 1:
        return fail(
            result,
            check="comparison_subjects",
            reason=(
                "Each comparison subject must resolve to exactly one value; "
                f"got {len(left_values)} and {len(right_values)}"
            ),
        )
    pass_check(result, "comparison_subjects")

    left = left_values[0]
    right = right_values[0]
    if not numbers_equal(left, spec.expected_left_value) or not numbers_equal(
        right, spec.expected_right_value
    ):
        return fail(
            result,
            check="comparison_values",
            reason="Replayed comparison operands do not match expected values",
        )
    pass_check(result, "comparison_values")

    relation = compare_numbers(left, spec.operator, right)
    if relation is not True:
        return fail(
            result,
            check="comparison_relation",
            reason=f"Declared relation {spec.operator} does not hold",
        )
    pass_check(result, "comparison_relation")

    if spec.delta_mode is not None and spec.expected_delta is not None:
        delta = derived_change(right, left, spec.delta_mode)
        if delta is None:
            return fail(
                result,
                check="comparison_delta",
                reason="Comparison delta is undefined (non-numeric or zero baseline)",
            )
        if not numbers_equal(delta, spec.expected_delta):
            return fail(
                result,
                check="comparison_delta",
                reason=(
                    f"Computed {spec.delta_mode} delta {delta} does not match "
                    f"{spec.expected_delta}"
                ),
            )
        pass_check(result, "comparison_delta")

    return finalize_claim(result)
