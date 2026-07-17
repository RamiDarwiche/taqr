import pytest
from sqlalchemy import text

from db import DB
from planner import PlanAgentOutput, QueryResponsePayload
from samples import download_datasets

TOP_K_QUERY_RESPONSE = {
    "query": "What is the most widely manufactured AI chip in the dataset?",
    "response": {
        "claims": [
            {
                "k": 1,
                "metric": "estimated_shipments_units",
                "filters": {},
                "subject": "NVIDIA H100",
                "claim_text": (
                    "The NVIDIA H100 is the most widely manufactured AI chip in "
                    "the dataset, with a total estimated shipment of 6,051,821 units."
                ),
                "claim_type": "ranking_top_k",
                "evidence_ids": ["e1"],
            }
        ],
        "evidence": [
            {
                "id": "e1",
                "sql": (
                    "SELECT chip_name, SUM(estimated_shipments_units) AS total_units "
                    "FROM ai_chip_market GROUP BY chip_name "
                    "ORDER BY total_units DESC LIMIT 1;"
                ),
                "rows": [["NVIDIA H100", 6051821]],
                "columns": ["chip_name", "total_units"],
                "row_count": 1,
                "result_fingerprint": (
                    "bc958e8fe1c83934593cadb445ade7974e5e28caec9da3d8f749e0928c505176"
                ),
            }
        ],
    },
}


@pytest.fixture
def top_k_plan_output():
    db = DB()
    download_datasets(db.get_engine())
    plan_output = PlanAgentOutput.model_validate(TOP_K_QUERY_RESPONSE["response"])
    try:
        yield db, plan_output
    finally:
        db.disconnect()


@pytest.fixture
def top_k_query_payload() -> QueryResponsePayload:
    return QueryResponsePayload.model_validate(TOP_K_QUERY_RESPONSE)
