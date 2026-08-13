"""Semantic verifier for presence and absence claims."""

from __future__ import annotations

from planner.types import Claim, ExistenceSpec
from verifier.base import fail, finalize_claim, pass_check
from verifier.context import VerificationContext
from verifier.domain_common import collect_rows, require_contract
from verifier.schemas import ClaimVerification
from verifier.sql_analysis import has_offset, to_decimal


def verify(
    claim: Claim,
    context: VerificationContext,
    result: ClaimVerification,
) -> ClaimVerification:
    spec = require_contract(
        claim,
        ExistenceSpec,
        result,
        prefix="existence",
        metric_required=False,
    )
    if spec is None:
        return result
    cited = context.cited(claim.evidence_ids)
    if not spec.exists and any(
        replay.query is not None and has_offset(replay.query) for replay in cited
    ):
        return fail(
            result,
            check="existence_sql_shape",
            reason="Absence cannot be proven by a query with OFFSET",
        )
    pass_check(result, "existence_sql_shape")

    if spec.mode == "rows":
        rows = [
            row
            for replay in cited
            if replay.rows is not None
            for row in replay.rows
        ]
        actual_exists = bool(rows)
        if actual_exists != spec.exists:
            return fail(
                result,
                check="existence_polarity",
                reason=(
                    f"Row evidence implies exists={actual_exists}, "
                    f"not {spec.exists}"
                ),
            )
        pass_check(result, "existence_polarity")
        if spec.exists and claim.subject is not None:
            if not spec.subject_column:
                return fail(
                    result,
                    check="existence_subject",
                    reason="Present subject requires subject_column",
                )
            records = collect_rows(
                claim,
                context,
                [spec.subject_column],
                result,
                check="existence_subject",
            )
            if records is None:
                return result
            subjects = (
                claim.subject if isinstance(claim.subject, list) else [claim.subject]
            )
            actual_subjects = {row[spec.subject_column] for row in records}
            if any(subject not in actual_subjects for subject in subjects):
                return fail(
                    result,
                    check="existence_subject",
                    reason="Claimed present subject is absent from evidence",
                )
            pass_check(result, "existence_subject")
        return finalize_claim(result)

    if not spec.result_column:
        return fail(
            result,
            check="existence_contract",
            reason=f"{spec.mode} evidence requires result_column",
        )
    records = collect_rows(
        claim,
        context,
        [spec.result_column],
        result,
        check="existence_value",
    )
    if records is None:
        return result
    if len(records) != 1:
        return fail(
            result,
            check="existence_value",
            reason=f"{spec.mode} evidence must return exactly one row",
        )
    value = records[0][spec.result_column]
    if spec.mode == "count":
        count = to_decimal(value)
        if count is None or count < 0 or count != count.to_integral_value():
            return fail(
                result,
                check="existence_value",
                reason=f"Existence count must be a non-negative integer: {value!r}",
            )
        actual_exists = count > 0
    else:
        if not isinstance(value, bool):
            return fail(
                result,
                check="existence_value",
                reason=f"Boolean existence evidence returned {value!r}",
            )
        actual_exists = value
    if actual_exists != spec.exists:
        return fail(
            result,
            check="existence_polarity",
            reason=f"Evidence implies exists={actual_exists}, not {spec.exists}",
        )
    pass_check(result, "existence_value")
    pass_check(result, "existence_polarity")
    return finalize_claim(result)
