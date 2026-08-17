"""Semantic verifier for categorical distributions."""

from __future__ import annotations

from decimal import Decimal

from domain_types import Claim, DistributionSpec
from verifier.base import confirm, finalize_claim, inconclusive, refute
from verifier.context import VerificationContext
from verifier.domain_common import collect_rows, resolve_spec, verify_untyped
from verifier.resolve import subject_list, values_equal
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import (
    aggregate_names,
    has_group_by,
    numbers_equal,
    to_decimal,
)


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    spec = resolve_spec(
        claim,
        DistributionSpec,
        result,
        prefix="distribution",
    )
    if spec is None:
        verify_untyped(claim, context, result, prefix="distribution")
        return finalize_claim(result)
    cited = context.cited(claim.evidence_ids)
    if spec.complete and any(
        replay.query is None
        or not has_group_by(replay.query)
        or not aggregate_names(replay.query)
        for replay in cited
    ):
        # Completeness cannot be established from evidence that does not
        # aggregate over groups, but nothing here contradicts the values.
        inconclusive(
            result,
            check="distribution_sql_shape",
            note=(
                "distribution is declared complete but its evidence is not a "
                "grouped aggregate, so full coverage cannot be established"
            ),
        )
    else:
        confirm(result, "distribution_sql_shape")

    records = collect_rows(
        claim,
        context,
        [spec.category_column, spec.value_column],
        result,
        check="distribution_columns",
    )
    if records is None:
        return finalize_claim(result)
    actual: dict[str, object] = {}
    for row in records:
        category = row[spec.category_column]
        if category is None:
            return refute(
                result,
                check="distribution_categories",
                reason="Distribution categories cannot be NULL",
            )
        key = _canonical_key(actual, category)
        if key in actual:
            return refute(
                result,
                check="distribution_categories",
                reason=f"Distribution category {key!r} is duplicated",
            )
        actual[key] = row[spec.value_column]

    resolved = {
        expected: _canonical_key(actual, expected)
        for expected in spec.expected_values
    }
    absent = sorted(name for name, key in resolved.items() if key not in actual)
    if absent:
        return refute(
            result,
            check="distribution_categories",
            reason=f"Expected distribution categories are absent from evidence: {absent}",
        )
    extra = sorted(set(actual) - {key for key in resolved.values() if key})
    if spec.complete and extra:
        return refute(
            result,
            check="distribution_categories",
            reason=(
                f"Distribution is declared complete but evidence holds "
                f"additional categories {extra}"
            ),
        )
    if claim.subject is not None:
        subjects = subject_list(claim.subject)
        unlisted = [
            name
            for name in spec.expected_values
            if not any(values_equal(name, subject) for subject in subjects)
        ]
        if unlisted:
            inconclusive(
                result,
                check="distribution_categories",
                note=(
                    f"claim subject {claim.subject!r} does not list contract "
                    f"categories {unlisted!r}"
                ),
            )
    confirm(result, "distribution_categories")

    for category, expected in spec.expected_values.items():
        value = actual[resolved[category]]
        numeric = to_decimal(value)
        if numeric is None or not numbers_equal(value, expected):
            return refute(
                result,
                check="distribution_values",
                reason=(
                    f"Value {value!r} for category {category!r} does not match "
                    f"expected {expected!r}"
                ),
            )
        if spec.value_mode == "count" and (
            numeric < 0 or numeric != numeric.to_integral_value()
        ):
            return refute(
                result,
                check="distribution_values",
                reason=f"Count for category {category!r} is not a non-negative integer",
            )
        if spec.value_mode == "share" and not Decimal(0) <= numeric <= Decimal(1):
            return refute(
                result,
                check="distribution_values",
                reason=f"Share for category {category!r} is outside [0, 1]",
            )
        if spec.value_mode == "percent" and not Decimal(0) <= numeric <= Decimal(100):
            return refute(
                result,
                check="distribution_values",
                reason=f"Percent for category {category!r} is outside [0, 100]",
            )
    confirm(result, "distribution_values")

    if spec.complete and spec.value_mode in {"share", "percent"}:
        target = Decimal(1) if spec.value_mode == "share" else Decimal(100)
        all_values = [to_decimal(value) for value in actual.values()]
        if any(value is None for value in all_values) or not numbers_equal(
            sum(value for value in all_values if value is not None),
            target,
        ):
            return refute(
                result,
                check="distribution_total",
                reason=f"Complete distribution does not sum to {target}",
            )
        confirm(result, "distribution_total")
    elif not spec.complete:
        inconclusive(
            result,
            check="distribution_coverage",
            note="Distribution contract intentionally covers only a subset",
        )

    return finalize_claim(result)


def _canonical_key(actual: dict[str, object], category: object) -> str:
    """Key a category by its own text, reusing an equivalent existing key.

    Category labels arrive as text from the planner and as native types from the
    database, so ``2024`` and ``"2024"`` must land on the same key.
    """
    for existing in actual:
        if values_equal(existing, category):
            return existing
    return str(category)
