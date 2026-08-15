"""Semantic verifier for aggregate claims."""

from __future__ import annotations

from decimal import Decimal

from domain_types import AggregationSpec, Claim
from verifier.base import fail, finalize_claim, pass_check
from verifier.context import VerificationContext
from verifier.domain_common import collect_rows, require_contract
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import (
    aggregate_names,
    column_index,
    has_group_by,
    numbers_equal,
    to_decimal,
)


def _matches_operation(operation: str, aggregate_functions: set[str]) -> bool:
    if operation in aggregate_functions:
        return True
    # An average/percentage is often written as SUM(indicator) / COUNT(*)
    # instead of AVG(indicator). Both forms compute the same aggregate shape.
    return operation == "AVG" and {"SUM", "COUNT"} <= aggregate_functions


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    spec = require_contract(
        claim,
        AggregationSpec,
        result,
        prefix="aggregation",
    )
    if spec is None:
        return result

    cited = context.cited(claim.evidence_ids)
    relevant = [
        replay
        for replay in cited
        if column_index(replay.evidence, spec.value_column) is not None
    ]
    if not relevant:
        return fail(
            result,
            check="aggregation_columns",
            reason=(
                f"No cited evidence resolves required column "
                f"{spec.value_column!r}"
            ),
        )
    if any(
        replay.query is None
        or not _matches_operation(spec.operation, aggregate_names(replay.query))
        for replay in relevant
    ):
        return fail(
            result,
            check="aggregation_sql_shape",
            reason=f"Evidence does not compute declared {spec.operation} aggregate",
        )
    if spec.scope == "scalar" and any(
        replay.query is not None and has_group_by(replay.query) for replay in relevant
    ):
        return fail(
            result,
            check="aggregation_sql_shape",
            reason="Scalar aggregation evidence must not contain GROUP BY",
        )
    if spec.scope == "grouped" and any(
        replay.query is not None and not has_group_by(replay.query)
        for replay in relevant
    ):
        return fail(
            result,
            check="aggregation_sql_shape",
            reason="Grouped aggregation evidence requires GROUP BY",
        )
    pass_check(result, "aggregation_sql_shape")

    columns = [spec.value_column]
    if spec.scope == "grouped":
        if not spec.subject_column or not isinstance(claim.subject, str):
            return fail(
                result,
                check="aggregation_subject",
                reason="Grouped aggregation requires one subject and subject_column",
            )
        columns.append(spec.subject_column)
    records = collect_rows(
        claim,
        context,
        columns,
        result,
        check="aggregation_columns",
    )
    if records is None:
        return result

    if spec.scope == "scalar":
        matches = records
        if len(matches) != 1:
            return fail(
                result,
                check="aggregation_cardinality",
                reason=f"Scalar aggregation expected one row, got {len(matches)}",
            )
    else:
        matches = [
            row for row in records if row[spec.subject_column] == claim.subject
        ]
        if len(matches) != 1:
            return fail(
                result,
                check="aggregation_subject",
                reason=f"Grouped subject must resolve to one row, got {len(matches)}",
            )
        pass_check(result, "aggregation_subject")
    pass_check(result, "aggregation_cardinality")

    actual = matches[0][spec.value_column]
    if not numbers_equal(actual, spec.expected_value):
        return fail(
            result,
            check="aggregation_value",
            reason=(
                f"Aggregate value {actual!r} does not match expected "
                f"{spec.expected_value!r}"
            ),
        )
    numeric = to_decimal(actual)
    if numeric is None:
        return fail(
            result,
            check="aggregation_value",
            reason=f"Aggregate value is not numeric: {actual!r}",
        )
    if spec.operation == "COUNT" and numeric != numeric.to_integral_value():
        return fail(
            result,
            check="aggregation_invariant",
            reason=f"COUNT result must be integral, got {actual!r}",
        )
    if (spec.operation == "COUNT" or spec.non_negative) and numeric < Decimal(0):
        return fail(
            result,
            check="aggregation_invariant",
            reason=f"Aggregate result must be non-negative, got {actual!r}",
        )
    pass_check(result, "aggregation_value")
    pass_check(result, "aggregation_invariant")
    return finalize_claim(result)
