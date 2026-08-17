from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import create_engine, text

from domain_types import Claim, ClaimType, Evidence, VerificationStatus
from tests.verifier.conftest import outcome_of
from verifier.outcome import CheckOutcome
from verifier.schemas import ClaimVerification
from verifier.top_k_ranking import (
    _check_monotonic,
    _check_top_k_row_count,
    verify_top_k_ranking,
)


def _ranking_sql(
    pairs: list[tuple[str | None, int]],
    *,
    order: str = "DESC",
    limit: int | None = None,
) -> str:
    selects = " UNION ALL ".join(
        f"SELECT {repr(name) if name is not None else 'NULL'} AS subject, "
        f"{score} AS score"
        for name, score in pairs
    )
    lim = len(pairs) if limit is None else limit
    return (
        f"SELECT * FROM ({selects}) AS ranking " f"ORDER BY score {order} LIMIT {lim}"
    )


def _verify(
    *,
    subject: str | list[str] | None,
    pairs: list[tuple[str | None, int]],
    k: int,
    metric: str | None = "score",
    filters: dict[str, Any] | None = None,
    order: str = "DESC",
    limit: int | None = None,
    sql: str | None = None,
    columns: list[str] | None = None,
) -> ClaimVerification:
    engine = create_engine("sqlite://")
    evidence_sql = sql or _ranking_sql(pairs, order=order, limit=limit)
    with engine.connect() as conn:
        replayed = [list(row) for row in conn.execute(text(evidence_sql)).fetchall()]

    claim = Claim(
        claim_text="Top-k ranking claim",
        claim_type=ClaimType.RANKING_TOP_K,
        subject=subject,
        metric=metric,
        k=k,
        filters=filters or {},
        evidence_ids=["e1"],
    )
    evidence = [
        Evidence(
            id="e1",
            sql=evidence_sql,
            rows=replayed,
            row_count=len(replayed),
            columns=columns or ["subject", "score"],
        )
    ]
    result = ClaimVerification(
        claim_id=claim.id,
        status=VerificationStatus.NOT_VERIFIED,
    )
    try:
        return verify_top_k_ranking(claim, evidence, engine, result)
    finally:
        engine.dispose()


def test_top_k_ordered_subjects_pass():
    result = _verify(
        subject=["Alice", "Bob"],
        pairs=[("Alice", 10), ("Bob", 9)],
        k=2,
    )

    assert result.status == VerificationStatus.VERIFIED
    assert "top_k_subject" in result.checks
    assert "top_k_sql_shape" in result.checks
    assert "top_k_monotonic" in result.checks


def test_top_k_fails_when_subjects_are_reordered():
    result = _verify(
        subject=["Bob", "Alice"],
        pairs=[("Alice", 10), ("Bob", 9)],
        k=2,
    )

    assert result.status == VerificationStatus.FAILED
    assert "top_k_subject" in result.checks
    assert "Subject order mismatch" in (result.failure_reason or "")


def test_top_k_fails_when_any_subject_is_missing():
    result = _verify(
        subject=["Alice", "Carol"],
        pairs=[("Alice", 10), ("Bob", 9)],
        k=2,
    )

    assert result.status == VerificationStatus.FAILED
    assert "top_k_subject" in result.checks


def test_top_k_sql_shape_requires_order_by():
    result = _verify(
        subject=["Alice", "Bob"],
        pairs=[("Alice", 10), ("Bob", 9)],
        k=2,
        sql=(
            "SELECT 'Alice' AS subject, 10 AS score "
            "UNION ALL SELECT 'Bob' AS subject, 9 AS score "
            "LIMIT 2"
        ),
    )

    assert result.status == VerificationStatus.FAILED
    assert result.checks == ["top_k_sql_shape"]
    assert "missing ORDER BY" in (result.failure_reason or "")


def test_top_k_limit_mismatch_is_fragile_not_failed():
    # Over-fetching does not make the claim about the leading row false, so the
    # shape mismatch is reported without failing the claim.
    result = _verify(
        subject=["Alice"],
        pairs=[("Alice", 10), ("Bob", 9)],
        k=1,
        sql=(
            "SELECT * FROM ("
            "SELECT 'Alice' AS subject, 10 AS score "
            "UNION ALL SELECT 'Bob' AS subject, 9 AS score"
            ") AS ranking ORDER BY score DESC LIMIT 2"
        ),
    )

    assert result.status == VerificationStatus.PARTIALLY_VERIFIED
    assert outcome_of(result, "top_k_sql_shape") is CheckOutcome.INCONCLUSIVE
    assert any("LIMIT 2 does not match claim k=1" in note for note in result.fragility_notes)


def test_top_k_subject_prefix_matches_a_longer_result():
    result = _verify(
        subject=["Alice"],
        pairs=[("Alice", 10), ("Bob", 9)],
        k=2,
    )

    assert result.status == VerificationStatus.PARTIALLY_VERIFIED
    assert outcome_of(result, "top_k_subject") is CheckOutcome.INCONCLUSIVE


def test_top_k_subject_matches_across_value_types():
    # A numeric business key claimed as text is not a contradiction.
    result = _verify(
        subject=["12", "7"],
        pairs=[("12", 10), ("7", 9)],
        k=2,
        sql=(
            "SELECT * FROM ("
            "SELECT 12 AS subject, 10 AS score "
            "UNION ALL SELECT 7 AS subject, 9 AS score"
            ") AS ranking ORDER BY score DESC LIMIT 2"
        ),
    )

    assert result.status == VerificationStatus.VERIFIED
    assert outcome_of(result, "top_k_subject") is CheckOutcome.CONFIRMED


def test_top_k_accepts_order_by_the_metric_expression():
    # ORDER BY the aggregate expression rather than its alias is the same order.
    result = _verify(
        subject=["Alice", "Bob"],
        pairs=[],
        k=2,
        metric="score",
        sql=(
            "SELECT subject, SUM(score) AS score FROM ("
            "SELECT 'Alice' AS subject, 10 AS score "
            "UNION ALL SELECT 'Bob' AS subject, 9 AS score"
            ") AS ranking GROUP BY subject ORDER BY SUM(score) DESC LIMIT 2"
        ),
    )

    assert result.status == VerificationStatus.VERIFIED
    assert outcome_of(result, "top_k_sql_shape") is CheckOutcome.CONFIRMED


def test_top_k_under_k_is_partially_verified():
    result = _verify(
        subject=["Alice"],
        pairs=[("Alice", 10)],
        k=2,
        limit=2,
    )

    assert result.status == VerificationStatus.PARTIALLY_VERIFIED
    assert "top_k_row_count" in result.checks
    assert any("expected 2 rows, got 1" in note for note in result.fragility_notes)


def test_top_k_over_k_is_partially_verified():
    claim_result = ClaimVerification(
        claim_id=uuid.uuid4(),
        status=VerificationStatus.NOT_VERIFIED,
    )
    evidence = Evidence(
        id="e1",
        sql="SELECT 1",
        rows=[],
        row_count=0,
        columns=["subject", "score"],
    )
    # LIMIT k normally caps rows; exercise the soft over-k path directly.
    result = _check_top_k_row_count(
        2,
        [["Alice", 10], ["Bob", 9], ["Carol", 8]],
        evidence,
        claim_result,
    )
    assert result.status == VerificationStatus.PARTIALLY_VERIFIED
    assert any("expected 2 rows, got 3" in note for note in result.fragility_notes)


def test_top_k_monotonic_fails_when_scores_out_of_order():
    # Ordering by the subject is rejected before misleading monotonic values
    # can make a ranking appear valid.
    sql = (
        "SELECT * FROM ("
        "SELECT 'Bob' AS subject, 5 AS score "
        "UNION ALL SELECT 'Alice' AS subject, 10 AS score"
        ") AS ranking ORDER BY subject DESC LIMIT 2"
    )
    result = _verify(
        subject=["Bob", "Alice"],
        pairs=[("Bob", 5), ("Alice", 10)],
        k=2,
        sql=sql,
    )

    assert result.status == VerificationStatus.FAILED
    assert outcome_of(result, "top_k_sql_shape") is CheckOutcome.REFUTED
    assert "rather than the claimed metric 'score'" in (result.failure_reason or "")


def test_top_k_monotonic_helper_rejects_out_of_order_scores():
    result = ClaimVerification(
        claim_id=uuid.uuid4(),
        status=VerificationStatus.NOT_VERIFIED,
    )
    evidence = Evidence(
        id="e1",
        sql="SELECT subject, score FROM ranking ORDER BY score DESC LIMIT 2",
        rows=[],
        row_count=0,
        columns=["subject", "score"],
    )

    _check_monotonic([["A", 5], ["B", 10]], 1, "DESC", evidence, result)

    assert result.status == VerificationStatus.FAILED
    assert "top_k_monotonic" in result.checks


def test_top_k_ties_are_partially_verified():
    result = _verify(
        subject=["Alice", "Bob"],
        pairs=[("Alice", 10), ("Bob", 10)],
        k=2,
    )

    assert result.status == VerificationStatus.PARTIALLY_VERIFIED
    assert "top_k_ties" in result.checks
    assert any("adjacent equal scores" in note for note in result.fragility_notes)


def test_top_k_null_subject_fails():
    result = _verify(
        subject=["Bob"],
        pairs=[(None, 10), ("Bob", 9)],
        k=2,
    )

    assert result.status == VerificationStatus.FAILED
    assert "top_k_null_subject" in result.checks


def test_top_k_negative_metric_fails():
    result = _verify(
        subject=["Alice", "Bob"],
        pairs=[("Alice", 10), ("Bob", -1)],
        k=2,
    )

    assert result.status == VerificationStatus.FAILED
    assert "top_k_non_negative" in result.checks


def test_top_k_unlocatable_filter_is_fragile_not_failed():
    # A filter the verifier cannot locate is unconfirmed, not contradicted.
    result = _verify(
        subject=["Alice"],
        pairs=[("Alice", 10)],
        k=1,
        filters={"quarter": "2025-Q4"},
    )

    assert result.status == VerificationStatus.PARTIALLY_VERIFIED
    assert outcome_of(result, "top_k_filters") is CheckOutcome.INCONCLUSIVE


def test_top_k_conflicting_filter_fails():
    # The claim says 2025-Q4 while the evidence restricts the column to 2024-Q1.
    result = _verify(
        subject=["Alice"],
        pairs=[("Alice", 10)],
        k=1,
        filters={"quarter": "2025-Q4"},
        sql=(
            "SELECT * FROM ("
            "SELECT 'Alice' AS subject, 10 AS score, '2024-Q1' AS quarter"
            ") AS ranking WHERE quarter = '2024-Q1' "
            "ORDER BY score DESC LIMIT 1"
        ),
        columns=["subject", "score", "quarter"],
    )

    assert result.status == VerificationStatus.FAILED
    assert outcome_of(result, "top_k_filters_conflict") is CheckOutcome.REFUTED


def test_top_k_filters_pass_when_present_in_sql():
    sql = (
        "SELECT * FROM ("
        "SELECT 'Alice' AS subject, 10 AS score"
        ") AS ranking "
        "WHERE '2025-Q4' = '2025-Q4' "
        "ORDER BY score DESC LIMIT 1"
    )
    result = _verify(
        subject=["Alice"],
        pairs=[("Alice", 10)],
        k=1,
        filters={"quarter": "2025-Q4"},
        sql=sql,
    )

    assert result.status == VerificationStatus.VERIFIED
    assert "top_k_filters" in result.checks
