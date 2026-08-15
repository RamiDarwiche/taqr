from __future__ import annotations

from decimal import Decimal
from typing import Union
from enum import Enum

Numeric = Union[int, float, Decimal]


class ClaimType(str, Enum):
    RANKING_TOP_K = "RANKING_TOP_K"
    AGGREGATION = "AGGREGATION"
    COMPARISON = "COMPARISON"
    TREND = "TREND"
    EXISTENCE = "EXISTENCE"
    DISTRIBUTION = "DISTRIBUTION"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    FAILED = "FAILED"


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EventType(str, Enum):
    QUERY = "QUERY"
    TOOL_CALL = "TOOL_CALL"
    QUERY_PLAN = "QUERY_PLAN"
    QUERY_VERIFICATION = "QUERY_VERIFICATION"
    QUERY_JUDGE = "QUERY_JUDGE"
