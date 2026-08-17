"""Semantic verifier for presence and absence claims."""

from __future__ import annotations

from domain_types import Claim, ExistenceSpec
from verifier.base import confirm, finalize_claim, inconclusive, refute
from verifier.context import VerificationContext
from verifier.domain_common import (
    collect_rows,
    resolve_spec,
    resolve_subject_match,
    subject_in_predicates,
    verify_untyped,
)
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import has_offset, to_decimal


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    spec = resolve_spec(
        claim,
        ExistenceSpec,
        result,
        prefix="existence",
        metric_required=False,
    )
    if spec is None:
        verify_untyped(claim, context, result, prefix="existence")
        return finalize_claim(result)
    cited = context.cited(claim.evidence_ids)
    if not spec.exists and any(
        replay.query is not None and has_offset(replay.query) for replay in cited
    ):
        return refute(
            result,
            check="existence_sql_shape",
            reason="Absence cannot be proven by a query with OFFSET",
        )
    confirm(result, "existence_sql_shape")

    if spec.mode == "rows":
        rows = [
            row
            for replay in cited
            if replay.rows is not None
            for row in replay.rows
        ]
        actual_exists = bool(rows)
        if actual_exists != spec.exists:
            return refute(
                result,
                check="existence_polarity",
                reason=(
                    f"Row evidence implies exists={actual_exists}, "
                    f"not {spec.exists}"
                ),
            )
        confirm(result, "existence_polarity")
        if spec.exists and claim.subject is not None:
            _verify_present_subject(claim, context, spec, result)
        return finalize_claim(result)

    if not spec.result_column:
        inconclusive(
            result,
            check="existence_contract",
            note=(
                f"{spec.mode} existence evidence names no result_column, so the "
                f"count or flag cannot be read"
            ),
        )
        return finalize_claim(result)
    records = collect_rows(
        claim,
        context,
        [spec.result_column],
        result,
        check="existence_value",
    )
    if records is None:
        return finalize_claim(result)
    if len(records) != 1:
        return refute(
            result,
            check="existence_value",
            reason=f"{spec.mode} evidence must return exactly one row",
        )
    value = records[0][spec.result_column]
    if spec.mode == "count":
        count = to_decimal(value)
        if count is None or count < 0 or count != count.to_integral_value():
            return refute(
                result,
                check="existence_value",
                reason=f"Existence count must be a non-negative integer: {value!r}",
            )
        actual_exists = count > 0
    else:
        if not isinstance(value, bool):
            return refute(
                result,
                check="existence_value",
                reason=f"Boolean existence evidence returned {value!r}",
            )
        actual_exists = value
    if actual_exists != spec.exists:
        return refute(
            result,
            check="existence_polarity",
            reason=f"Evidence implies exists={actual_exists}, not {spec.exists}",
        )
    confirm(result, "existence_value")
    confirm(result, "existence_polarity")
    return finalize_claim(result)


def _verify_present_subject(
    claim: Claim,
    context: VerificationContext,
    spec: ExistenceSpec,
    result: ClaimVerification,
) -> None:
    """Check that a subject asserted to be present occurs in the evidence.

    ``subject_column`` is a hint, not a requirement. A subject stored across
    columns — a forename beside a surname — is located by searching each column
    and each run of adjacent columns, so the planner is never asked to name a
    single column that does not exist.

    A subject may also be pinned by the query instead of projected: the rows of
    ``SELECT q1 ... WHERE forename = 'Bruno' AND surname = 'Senna'`` are Bruno
    Senna's without naming him. Only a subject that appears in neither the rows
    nor the predicates refutes the claim.
    """
    if resolve_subject_match(
        claim,
        context,
        result,
        check="existence_subject",
        preferred_column=spec.subject_column,
    ):
        return
    if subject_in_predicates(claim, context):
        confirm(
            result,
            "existence_subject",
            detail=(
                f"subject {claim.subject!r} is pinned by evidence predicates "
                f"rather than projected"
            ),
        )
        return
    refute(
        result,
        check="existence_subject",
        reason=(
            f"Claimed present subject {claim.subject!r} appears in neither the "
            f"replayed rows nor the predicates of the cited evidence"
        ),
    )
