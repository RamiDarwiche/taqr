"""Semantic verifier for aggregate claims."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from domain_types import AggregationSpec, Claim
from verifier.base import confirm, finalize_claim, inconclusive, refute
from verifier.context import EvidenceReplay, VerificationContext
from verifier.domain_common import (
    collect_rows,
    replay_columns,
    resolve_replay_column,
    resolve_spec,
    verify_untyped,
)
from verifier.resolve import resolve_subject, values_equal
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import (
    aggregate_names,
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
    spec = resolve_spec(
        claim,
        AggregationSpec,
        result,
        prefix="aggregation",
    )
    if spec is None:
        verify_untyped(claim, context, result, prefix="aggregation")
        return finalize_claim(result)

    cited = context.cited(claim.evidence_ids)
    relevant = [
        replay
        for replay in cited
        if resolve_replay_column(replay, spec.value_column) is not None
    ]
    if not relevant:
        inconclusive(
            result,
            check="aggregation_columns",
            note=f"no cited evidence resolves value column {spec.value_column!r}",
        )
        return finalize_claim(result)
    if any(
        replay.query is None
        or not _matches_operation(spec.operation, aggregate_names(replay.query))
        for replay in relevant
    ):
        return refute(
            result,
            check="aggregation_sql_shape",
            reason=f"Evidence does not compute declared {spec.operation} aggregate",
        )
    confirm(result, "aggregation_sql_shape")

    # The declared scope is a description of the evidence, and the evidence
    # itself says which shape it has. A breakdown query cited by a claim about
    # one group is grouped evidence regardless of what the spec says.
    grouped = any(
        replay.query is not None and has_group_by(replay.query) for replay in relevant
    )
    scope = "grouped" if grouped else "scalar"
    if scope != spec.scope:
        inconclusive(
            result,
            check="aggregation_scope",
            note=(
                f"contract declares {spec.scope} scope but the evidence is "
                f"{scope}; verifying as {scope}"
            ),
        )
    else:
        confirm(result, "aggregation_scope")

    columns = [spec.value_column]
    selector_column, selector_value = _row_selector(claim, spec, relevant)
    if scope == "grouped":
        if selector_column is None:
            inconclusive(
                result,
                check="aggregation_subject",
                note=(
                    "grouped aggregation evidence has no subject or filter that "
                    "identifies which group the claim is about"
                ),
            )
            return finalize_claim(result)
        columns.append(selector_column)

    records = collect_rows(
        claim,
        context,
        columns,
        result,
        check="aggregation_columns",
    )
    if records is None:
        return finalize_claim(result)

    if scope == "scalar":
        matches = records
        if len(matches) != 1:
            return refute(
                result,
                check="aggregation_cardinality",
                reason=f"Scalar aggregation expected one row, got {len(matches)}",
            )
    else:
        matches = [
            row
            for row in records
            if values_equal(row.get(selector_column), selector_value)
        ]
        if len(matches) != 1:
            return refute(
                result,
                check="aggregation_subject",
                reason=(
                    f"Group {selector_value!r} must resolve to one row in "
                    f"{selector_column!r}, got {len(matches)}"
                ),
            )
        confirm(result, "aggregation_subject")
    confirm(result, "aggregation_cardinality")

    actual = matches[0][spec.value_column]
    if not numbers_equal(actual, spec.expected_value):
        return refute(
            result,
            check="aggregation_value",
            reason=(
                f"Aggregate value {actual!r} does not match expected "
                f"{spec.expected_value!r}"
            ),
        )
    numeric = to_decimal(actual)
    if numeric is None:
        return refute(
            result,
            check="aggregation_value",
            reason=f"Aggregate value is not numeric: {actual!r}",
        )
    if spec.operation == "COUNT" and numeric != numeric.to_integral_value():
        return refute(
            result,
            check="aggregation_invariant",
            reason=f"COUNT result must be integral, got {actual!r}",
        )
    if (spec.operation == "COUNT" or spec.non_negative) and numeric < Decimal(0):
        return refute(
            result,
            check="aggregation_invariant",
            reason=f"Aggregate result must be non-negative, got {actual!r}",
        )
    confirm(result, "aggregation_value")
    confirm(result, "aggregation_invariant")
    return finalize_claim(result)


def _row_selector(
    claim: Claim,
    spec: AggregationSpec,
    relevant: list[EvidenceReplay],
) -> tuple[str | None, Any]:
    """Which column and value identify the row a grouped claim is about.

    A subject located in the grouped rows is the strongest form. When there is
    no subject — "203 accounts have status 'A'" — the claim's own filters name
    the group, so a filter whose key resolves to a projected column selects the
    row. Without either, the claim does not say which group it means.
    """
    subjects = claim.subject if isinstance(claim.subject, list) else [claim.subject]
    subject = subjects[0] if len(subjects) == 1 else None

    if subject is not None:
        for replay in relevant:
            if replay.rows is None:
                continue
            match = resolve_subject(
                subject,
                replay_columns(replay),
                replay.rows,
                preferred_column=spec.subject_column,
            )
            if match is not None and not match.is_composite:
                return match.columns[0], subject
        return (spec.subject_column, subject) if spec.subject_column else (None, None)

    for replay in relevant:
        for key, value in claim.filters.items():
            if value is None:
                continue
            match = resolve_replay_column(replay, key)
            value_match = resolve_replay_column(replay, spec.value_column)
            if match is not None and (
                value_match is None or match.index != value_match.index
            ):
                return match.name, value
    return None, None
