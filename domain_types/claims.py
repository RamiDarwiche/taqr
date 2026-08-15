from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from domain_types.common import ClaimType
from domain_types.common import Numeric


class Claim(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    claim_text: str
    claim_type: ClaimType
    subject: str | list[str] | None = None
    metric: str | None = None
    k: int | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str]
    verification_spec: VerificationSpec | None = None

    @model_validator(mode="after")
    def validate_verification_spec_kind(self) -> Claim:
        if (
            self.verification_spec is not None
            and self.verification_spec.kind != self.claim_type.value
        ):
            raise ValueError(
                "verification_spec.kind must match claim_type "
                f"({self.verification_spec.kind!r} != {self.claim_type.value!r})"
            )
        return self


class Evidence(BaseModel):
    id: str
    sql: str
    rows: list[list[Any]]
    row_count: int
    columns: list[str]
    # null from the model; provenance fills a hex digest after the run
    result_fingerprint: str | None = None


class AggregationSpec(BaseModel):
    kind: Literal["AGGREGATION"] = "AGGREGATION"
    operation: Literal["SUM", "COUNT", "AVG", "MIN", "MAX"]
    value_column: str
    expected_value: Numeric
    scope: Literal["scalar", "grouped"] = "scalar"
    subject_column: str | None = None
    non_negative: bool = False


class ComparisonSpec(BaseModel):
    kind: Literal["COMPARISON"] = "COMPARISON"
    left_subject: str
    right_subject: str
    subject_column: str
    value_column: str
    operator: Literal["GT", "GTE", "LT", "LTE", "EQ", "NE"]
    expected_left_value: Numeric
    expected_right_value: Numeric
    delta_mode: Literal["absolute", "percent"] | None = None
    expected_delta: Numeric | None = None

    @model_validator(mode="after")
    def validate_delta(self) -> ComparisonSpec:
        if (self.delta_mode is None) != (self.expected_delta is None):
            raise ValueError("delta_mode and expected_delta must be provided together")
        return self


class TrendSpec(BaseModel):
    kind: Literal["TREND"] = "TREND"
    time_column: str
    value_column: str
    start_period: str
    end_period: str
    expected_start_value: Numeric
    expected_end_value: Numeric
    direction: Literal["increased", "decreased", "unchanged"]
    change_mode: Literal["absolute", "percent"] | None = None
    expected_change: Numeric | None = None
    require_monotonic: bool = False

    @model_validator(mode="after")
    def validate_change(self) -> TrendSpec:
        if (self.change_mode is None) != (self.expected_change is None):
            raise ValueError(
                "change_mode and expected_change must be provided together"
            )
        if self.start_period == self.end_period:
            raise ValueError("start_period and end_period must differ")
        return self


class ExistenceSpec(BaseModel):
    kind: Literal["EXISTENCE"] = "EXISTENCE"
    exists: bool
    mode: Literal["rows", "count", "boolean"] = "rows"
    result_column: str | None = None
    subject_column: str | None = None


class DistributionSpec(BaseModel):
    kind: Literal["DISTRIBUTION"] = "DISTRIBUTION"
    category_column: str
    value_column: str
    value_mode: Literal["count", "share", "percent"]
    expected_values: dict[str, Numeric]
    complete: bool = True


VerificationSpec = Annotated[
    AggregationSpec | ComparisonSpec | TrendSpec | ExistenceSpec | DistributionSpec,
    Field(discriminator="kind"),
]
