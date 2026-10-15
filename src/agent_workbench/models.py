"""Strict trust-boundary schemas shared by recordings, adapters and the engine."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Usage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    synthetic: bool = False


class ToolCall(StrictModel):
    kind: Literal["tool"] = "tool"
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: Literal["inspect_dataset", "analyze", "read_evidence", "review", "publish"]
    arguments: dict[str, Any]


class EvidenceRef(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=100)
    field: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,100}$")


class NumericFact(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    value: float = Field(ge=-1e15, le=1e15)
    citation: EvidenceRef

    @field_validator("value")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("non-finite numeric fact")
        return value


class Report(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    explanation: str = Field(min_length=1, max_length=3000)
    facts: list[NumericFact] = Field(min_length=1, max_length=30)
    chart: list[NumericFact] = Field(max_length=30)
    no_data: bool = False


class Verdict(StrictModel):
    accepted: bool
    evidence_ids: list[str] = Field(min_length=1, max_length=10)
    reason: str = Field(min_length=1, max_length=1000)


class Final(StrictModel):
    kind: Literal["final"] = "final"
    answer: Report | Verdict | JudgeResult


Decision = Annotated[ToolCall | Final, Field(discriminator="kind")]


class Turn(StrictModel):
    decision: Decision
    usage: Usage | None = None


class AnalyzeArgs(StrictModel):
    code: str = Field(min_length=1, max_length=12000)


class ReadArgs(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=100)


class ReviewArgs(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=100)


class EmptyArgs(StrictModel):
    pass


class Limits(StrictModel):
    max_steps: int = Field(default=24, ge=1, le=100)
    max_tokens: int = Field(default=20000, ge=1, le=1000000)
    max_cost_usd: float = Field(default=1.0, ge=0, le=100)
    input_token_cap: int = Field(default=10000, ge=1, le=100000)
    output_token_cap: int = Field(default=1500, ge=1, le=10000)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    context_tokens: int = Field(default=12000, ge=1000, le=200000)
    provider_attempts: int = Field(default=2, ge=1, le=3)

    @field_validator("max_cost_usd", "input_price_per_million", "output_price_per_million")
    @classmethod
    def finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("prices and budget must be finite")
        return value

    @model_validator(mode="after")
    def possible_context(self) -> Limits:
        if self.output_token_cap >= self.context_tokens:
            raise ValueError("output allowance must leave room for input within context_tokens")
        return self


class RunConfig(StrictModel):
    mode: Literal["scripted", "openai", "anthropic"] = "scripted"
    model: str = Field(default="author-written-v1", min_length=1, max_length=100)
    scenario: str = Field(default="repair", pattern=r"^[a-z-]{1,40}$")
    limits: Limits = Limits()
    allow_live: bool = False


class JudgeResult(StrictModel):
    correctness: int = Field(ge=0, le=4)
    evidence: int = Field(ge=0, le=4)
    clarity: int = Field(ge=0, le=4)
    safety: int = Field(ge=0, le=4)
    explanation: str = Field(min_length=1, max_length=1500)
    provenance: Literal["author-written-scripted", "live-unverified", "live"]
    model: str
    prompt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


Final.model_rebuild()
Turn.model_rebuild()
