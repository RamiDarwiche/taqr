from decimal import Decimal

from sqlalchemy import create_engine, text

from domain_types import Evidence
from provenance.utils import (
    _canonicalize_value,
    attach_result_fingerprints,
    fingerprint_rows,
)


def test_fingerprint_numeric_types_match():
    expected = fingerprint_rows([[40, 40.5]])
    assert fingerprint_rows([[40.0, 40.5]]) == expected
    assert fingerprint_rows([[Decimal("40"), Decimal("40.5")]]) == expected
    assert fingerprint_rows([[Decimal("40.00"), Decimal("40.50")]]) == expected
    assert fingerprint_rows([["40", "40.5"]]) == expected
    assert fingerprint_rows([["40.00", "40.50"]]) == expected


def test_fingerprint_preserves_non_numeric_strings():
    assert fingerprint_rows([["Alice"]]) != fingerprint_rows([["Bob"]])
    assert _canonicalize_value("Alice") == "Alice"
    assert _canonicalize_value("2024-01-01") == "2024-01-01"


def test_canonicalize_keeps_bools_distinct_from_ints():
    assert _canonicalize_value(True) is True
    assert _canonicalize_value(False) is False
    assert fingerprint_rows([[True]]) != fingerprint_rows([[1]])


def test_truncated_llm_float_does_not_match_db_decimal():
    """AVG-style Decimals vs LLM-truncated floats must not silently match."""
    db_rows = [[Decimal("91.5238095238095238")]]
    llm_rows = [[91.5238095238095]]
    assert fingerprint_rows(db_rows) != fingerprint_rows(llm_rows)


def test_attach_result_fingerprints_uses_sql_replay_not_llm_rows():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE t (name TEXT, score REAL)"))
            conn.execute(
                text("INSERT INTO t VALUES ('a', 91.52380952380952), ('b', 40.0)")
            )
            conn.commit()

        evidence = Evidence(
            id="e1",
            sql="SELECT name, score FROM t ORDER BY score DESC",
            # Deliberately truncated LLM paste:
            rows=[["a", 91.5238095238095], ["b", 40]],
            row_count=2,
            columns=["name", "score"],
        )
        attach_result_fingerprints(engine, [evidence])

        with engine.connect() as conn:
            replayed = [
                list(row)
                for row in conn.execute(text(evidence.sql)).fetchall()
            ]
        assert evidence.result_fingerprint == fingerprint_rows(replayed)
        assert evidence.row_count == len(replayed)
        assert evidence.result_fingerprint != fingerprint_rows(
            [["a", 91.5238095238095], ["b", 40]]
        )
    finally:
        engine.dispose()
