"""Semantic verifier for categorical distributions."""

from __future__ import annotations

from decimal import Decimal

from planner.schemas import Claim, DistributionSpec
from verifier.base import fail, finalize_claim, mark_fragile, pass_check
from verifier.context import VerificationContext
from verifier.domain_common import collect_rows, require_contract
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
    spec = require_contract(
        claim,
        DistributionSpec,
        result,
        prefix="distribution",
    )
    if spec is None:
        return result
    cited = context.cited(claim.evidence_ids)
    if spec.complete and any(
        replay.query is None
        or not has_group_by(replay.query)
        or not aggregate_names(replay.query)
        for replay in cited
    ):
        return fail(
            result,
            check="distribution_sql_shape",
            reason="Complete distributions require grouped aggregate evidence",
        )
    pass_check(result, "distribution_sql_shape")

    records = collect_rows(
        claim,
        context,
        [spec.category_column, spec.value_column],
        result,
        check="distribution_columns",
    )
    if records is None:
        return result
    actual: dict[str, object] = {}
    for row in records:
        category = row[spec.category_column]
        if category is None:
            return fail(
                result,
                check="distribution_categories",
                reason="Distribution categories cannot be NULL",
            )
        key = str(category)
        if key in actual:
            return fail(
                result,
                check="distribution_categories",
                reason=f"Distribution category {key!r} is duplicated",
            )
        actual[key] = row[spec.value_column]

    expected_keys = set(spec.expected_values)
    actual_keys = set(actual)
    if spec.complete and actual_keys != expected_keys:
        return fail(
            result,
            check="distribution_categories",
            reason="Complete distribution categories differ from expected categories",
        )
    if not expected_keys.issubset(actual_keys):
        return fail(
            result,
            check="distribution_categories",
            reason="Expected distribution category is absent from evidence",
        )
    if claim.subject is not None:
        subjects = claim.subject if isinstance(claim.subject, list) else [claim.subject]
        if set(subjects) != expected_keys:
            return fail(
                result,
                check="distribution_categories",
                reason="Claim subjects must equal typed expected categories",
            )
    pass_check(result, "distribution_categories")

    for category, expected in spec.expected_values.items():
        value = actual[category]
        numeric = to_decimal(value)
        if numeric is None or not numbers_equal(value, expected):
            return fail(
                result,
                check="distribution_values",
                reason=f"Value for category {category!r} does not match expected",
            )
        if spec.value_mode == "count" and (
            numeric < 0 or numeric != numeric.to_integral_value()
        ):
            return fail(
                result,
                check="distribution_values",
                reason=f"Count for category {category!r} is not a non-negative integer",
            )
        if spec.value_mode == "share" and not Decimal(0) <= numeric <= Decimal(1):
            return fail(
                result,
                check="distribution_values",
                reason=f"Share for category {category!r} is outside [0, 1]",
            )
        if spec.value_mode == "percent" and not Decimal(0) <= numeric <= Decimal(100):
            return fail(
                result,
                check="distribution_values",
                reason=f"Percent for category {category!r} is outside [0, 100]",
            )
    pass_check(result, "distribution_values")

    if spec.complete and spec.value_mode in {"share", "percent"}:
        target = Decimal(1) if spec.value_mode == "share" else Decimal(100)
        all_values = [to_decimal(value) for value in actual.values()]
        if any(value is None for value in all_values) or not numbers_equal(
            sum(value for value in all_values if value is not None),
            target,
        ):
            return fail(
                result,
                check="distribution_total",
                reason=f"Complete distribution does not sum to {target}",
            )
        pass_check(result, "distribution_total")
    elif not spec.complete:
        mark_fragile(
            result,
            check="distribution_coverage",
            note="Distribution contract intentionally covers only a subset",
        )

    return finalize_claim(result)
