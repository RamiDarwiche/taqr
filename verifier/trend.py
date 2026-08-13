"""Semantic verifier for change-over-time claims."""

from __future__ import annotations

from planner.types import Claim, TrendSpec
from verifier.base import fail, finalize_claim, pass_check
from verifier.context import VerificationContext
from verifier.domain_common import collect_rows, require_contract
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import (
    derived_change,
    numbers_equal,
    order_direction,
    ordered_columns,
    to_decimal,
)


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    spec = require_contract(claim, TrendSpec, result, prefix="trend")
    if spec is None:
        return result
    for replay in context.cited(claim.evidence_ids):
        if (
            replay.rows is not None
            and len(replay.rows) > 1
            and (
                replay.query is None
                or order_direction(replay.query) != "ASC"
                or spec.time_column.casefold() not in ordered_columns(replay.query)
            )
        ):
            return fail(
                result,
                check="trend_sql_shape",
                reason="Multi-row trend evidence requires explicit ascending ORDER BY",
            )
    pass_check(result, "trend_sql_shape")
    records = collect_rows(
        claim,
        context,
        [spec.time_column, spec.value_column],
        result,
        check="trend_columns",
    )
    if records is None:
        return result
    if len(records) < 2:
        return fail(
            result,
            check="trend_periods",
            reason="Trend evidence requires at least two periods",
        )

    periods = [str(row[spec.time_column]) for row in records]
    if len(periods) != len(set(periods)):
        return fail(
            result,
            check="trend_periods",
            reason="Trend evidence contains duplicate periods",
        )
    if periods.count(spec.start_period) != 1 or periods.count(spec.end_period) != 1:
        return fail(
            result,
            check="trend_periods",
            reason="Start and end periods must each resolve exactly once",
        )
    start_index = periods.index(spec.start_period)
    end_index = periods.index(spec.end_period)
    if start_index >= end_index:
        return fail(
            result,
            check="trend_periods",
            reason="Trend evidence is not ordered from start period to end period",
        )
    pass_check(result, "trend_periods")

    start = records[start_index][spec.value_column]
    end = records[end_index][spec.value_column]
    if not numbers_equal(start, spec.expected_start_value) or not numbers_equal(
        end, spec.expected_end_value
    ):
        return fail(
            result,
            check="trend_values",
            reason="Trend endpoint values do not match the typed contract",
        )
    start_number = to_decimal(start)
    end_number = to_decimal(end)
    if start_number is None or end_number is None:
        return fail(
            result,
            check="trend_values",
            reason="Trend endpoints must be numeric",
        )
    pass_check(result, "trend_values")

    actual_direction = (
        "increased"
        if end_number > start_number
        else "decreased"
        if end_number < start_number
        else "unchanged"
    )
    if actual_direction != spec.direction:
        return fail(
            result,
            check="trend_direction",
            reason=(
                f"Trend direction is {actual_direction}, not declared "
                f"{spec.direction}"
            ),
        )
    pass_check(result, "trend_direction")

    if spec.change_mode is not None and spec.expected_change is not None:
        change = derived_change(start, end, spec.change_mode)
        if change is None:
            return fail(
                result,
                check="trend_change",
                reason="Trend change is undefined (non-numeric or zero baseline)",
            )
        if not numbers_equal(change, spec.expected_change):
            return fail(
                result,
                check="trend_change",
                reason=(
                    f"Computed {spec.change_mode} change {change} does not match "
                    f"{spec.expected_change}"
                ),
            )
        pass_check(result, "trend_change")

    if spec.require_monotonic:
        values = [
            to_decimal(row[spec.value_column])
            for row in records[start_index : end_index + 1]
        ]
        if any(value is None for value in values):
            return fail(
                result,
                check="trend_monotonic",
                reason="Monotonic trend values must be numeric",
            )
        pairs = zip(values, values[1:], strict=False)
        if spec.direction == "increased":
            monotonic = all(left <= right for left, right in pairs)
        elif spec.direction == "decreased":
            monotonic = all(left >= right for left, right in pairs)
        else:
            monotonic = all(left == right for left, right in pairs)
        if not monotonic:
            return fail(
                result,
                check="trend_monotonic",
                reason="Series is not monotonic in the declared direction",
            )
        pass_check(result, "trend_monotonic")

    return finalize_claim(result)
