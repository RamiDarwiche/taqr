"""Shared domain enums"""

from __future__ import annotations

from enum import Enum


class ClaimType(str, Enum):
    RANKING_TOP_K = "ranking_top_k"
    AGGREGATION = "aggregation"
    COMPARISON = "comparison"
    TREND = "trend"
    EXISTENCE = "existence"
    DISTRIBUTION = "distribution"


class VerificationStatus(str, Enum):
    VERIFIED = "fully_verified"
    PARTIALLY_VERIFIED = "partially_verified"
    NOT_VERIFIED = "not_verified"
    FAILED = "failed"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EventType(str, Enum):
    QUERY = "query"
    TOOL_CALL = "tool_call"
    QUERY_RESPONSE = "query_response"
