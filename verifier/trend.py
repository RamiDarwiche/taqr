"""Semantic verifier for change-over-time claims."""

from __future__ import annotations

from domain_types import Claim, TrendSpec
from verifier.base import confirm, finalize_claim, inconclusive, refute
from verifier.context import VerificationContext
from verifier.domain_common import collect_rows, resolve_spec, verify_untyped
from verifier.resolve import values_equal
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import (
    derived_change,
    numbers_equal,
    order_keys,
    reported_numbers_equal,
    to_decimal,
)


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    spec = resolve_spec(claim, TrendSpec, result, prefix="trend")
    if spec is None:
        verify_untyped(claim, context, result, prefix="trend")
        return finalize_claim(result)
    if _check_sql_shape(claim, context, spec, result) is False:
        return result
    records = collect_rows(
        claim,
        context,
        [spec.time_column, spec.value_column],
        result,
        check="trend_columns",
    )
    if records is None:
        return finalize_claim(result)
    if len(records) < 2:
        return refute(
            result,
            check="trend_periods",
            reason="Trend evidence requires at least two periods",
        )

    periods = [row[spec.time_column] for row in records]
    if _has_duplicates(periods):
        return refute(
            result,
            check="trend_periods",
            reason="Trend evidence contains duplicate periods",
        )
    start_index = _period_index(periods, spec.start_period)
    end_index = _period_index(periods, spec.end_period)
    if start_index is None or end_index is None:
        return refute(
            result,
            check="trend_periods",
            reason=(
                f"Start and end periods must each resolve exactly once; "
                f"{spec.start_period!r} and {spec.end_period!r} do not"
            ),
        )
    if start_index >= end_index:
        return refute(
            result,
            check="trend_periods",
            reason="Trend evidence is not ordered from start period to end period",
        )
    confirm(result, "trend_periods")

    start = records[start_index][spec.value_column]
    end = records[end_index][spec.value_column]
    if not numbers_equal(start, spec.expected_start_value) or not numbers_equal(
        end, spec.expected_end_value
    ):
        return refute(
            result,
            check="trend_values",
            reason=(
                f"Trend endpoints {start!r} → {end!r} do not match the declared "
                f"{spec.expected_start_value!r} → {spec.expected_end_value!r}"
            ),
        )
    start_number = to_decimal(start)
    end_number = to_decimal(end)
    if start_number is None or end_number is None:
        return refute(
            result,
            check="trend_values",
            reason="Trend endpoints must be numeric",
        )
    confirm(result, "trend_values")

    actual_direction = (
        "increased"
        if end_number > start_number
        else "decreased"
        if end_number < start_number
        else "unchanged"
    )
    if actual_direction != spec.direction:
        return refute(
            result,
            check="trend_direction",
            reason=(
                f"Trend direction is {actual_direction}, not declared "
                f"{spec.direction}"
            ),
        )
    confirm(result, "trend_direction")

    if spec.change_mode is not None and spec.expected_change is not None:
        change = derived_change(start, end, spec.change_mode)
        if change is None:
            return refute(
                result,
                check="trend_change",
                reason="Trend change is undefined (non-numeric or zero baseline)",
            )
        if not reported_numbers_equal(change, spec.expected_change):
            return refute(
                result,
                check="trend_change",
                reason=(
                    f"Computed {spec.change_mode} change {change} does not match "
                    f"{spec.expected_change}"
                ),
            )
        confirm(result, "trend_change")

    if spec.require_monotonic:
        values = [
            to_decimal(row[spec.value_column])
            for row in records[start_index : end_index + 1]
        ]
        if any(value is None for value in values):
            return refute(
                result,
                check="trend_monotonic",
                reason="Monotonic trend values must be numeric",
            )
        pairs = list(zip(values, values[1:], strict=False))
        if spec.direction == "increased":
            monotonic = all(left <= right for left, right in pairs)
        elif spec.direction == "decreased":
            monotonic = all(left >= right for left, right in pairs)
        else:
            monotonic = all(left == right for left, right in pairs)
        if not monotonic:
            return refute(
                result,
                check="trend_monotonic",
                reason="Series is not monotonic in the declared direction",
            )
        confirm(result, "trend_monotonic")

    return finalize_claim(result)


def _check_sql_shape(
    claim: Claim,
    context: VerificationContext,
    spec: TrendSpec,
    result: ClaimVerification,
) -> bool:
    """Validate that multi-row trend evidence has a reproducible row order.

    Without any statement-level ``ORDER BY`` the row order is arbitrary, so a
    series read positionally is not reproducible — a refutation. An order that
    exists but is not obviously ascending by the time column is merely
    unconfirmed: the period check below still validates that the start period
    precedes the end period in the rows actually returned.
    """
    for replay in context.cited(claim.evidence_ids):
        if replay.rows is None or len(replay.rows) <= 1 or replay.query is None:
            continue
        keys = order_keys(replay.query)
        if not keys:
            refute(
                result,
                check="trend_sql_shape",
                reason=(
                    f"Multi-row trend evidence {replay.evidence.id} has no "
                    f"ORDER BY, so its series order is not reproducible"
                ),
            )
            return False
        time_key = next(
            (key for key in keys if key.name == spec.time_column.casefold()), None
        )
        if time_key is None or time_key.desc:
            inconclusive(
                result,
                check="trend_sql_shape",
                note=(
                    f"trend evidence {replay.evidence.id} is not ordered "
                    f"ascending by {spec.time_column!r}; the series is read in "
                    f"returned row order"
                ),
            )
            return True
    confirm(result, "trend_sql_shape")
    return True


def _has_duplicates(periods: list[object]) -> bool:
    for index, period in enumerate(periods):
        if any(values_equal(period, other) for other in periods[index + 1 :]):
            return True
    return False


def _period_index(periods: list[object], wanted: str) -> int | None:
    matches = [
        index
        for index, period in enumerate(periods)
        if values_equal(period, wanted)
    ]
    return matches[0] if len(matches) == 1 else None
