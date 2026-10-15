"""Strict executed-benchmark artifact contract; booleans alone never satisfy a gate."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from .models import Report, StrictModel, Verdict
from .util import object_hash

Hash = str


class Pricing(StrictModel):
    input: float | None = Field(ge=0, allow_inf_nan=False)
    output: float | None = Field(ge=0, allow_inf_nan=False)


class Manifest(StrictModel):
    state_schema: Literal[1]
    engine_behavior: Literal["1"]
    package_version: str = Field(min_length=1)
    source_fingerprint: Hash
    sandbox_image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    prompts: dict[str, Hash]
    dataset_hash: Hash
    recording_hash: Hash
    config_hash: Hash
    provider: Literal["scripted"]
    model: Literal["author-written-v1"]
    pricing: Pricing

    @field_validator("source_fingerprint", "dataset_hash", "recording_hash", "config_hash")
    @classmethod
    def hash_value(cls, value: str) -> str:
        if re.fullmatch(r"[a-f0-9]{64}", value) is None:
            raise ValueError("expected SHA256")
        return value

    @field_validator("prompts")
    @classmethod
    def prompt_hashes(cls, values: dict[str, str]) -> dict[str, str]:
        if set(values) != {"parent-v1.txt", "reviewer-v1.txt", "judge-v1.txt"}:
            raise ValueError("missing/unknown versioned prompts")
        for value in values.values():
            cls.hash_value(value)
        return values


class Metrics(StrictModel):
    steps: int = Field(ge=1, le=24)
    charged_tokens: int = Field(ge=1, le=20000)
    charged_cost_usd: float = Field(ge=0, le=0)
    usage_unknown: Literal[False]
    actual_api_spend_usd: Literal[0]


class Barrier(StrictModel):
    committed: Literal[True]
    run_id: str


class Recovery(StrictModel):
    worker_pid: int = Field(ge=1)
    exitcode: Literal[-9]
    replacement_exitcode: Literal[0]
    barrier: Barrier
    analysis_launches_before: Literal[1]
    analysis_launches_after: Literal[1]


class CaseResult(StrictModel):
    scenario: str
    passed: bool
    failures: list[str]
    run_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    expected: dict[str, Any]
    observed_report: Report | None
    verdict: Verdict | None
    status: Literal["completed", "failed"]
    metrics: Metrics
    wall_seconds: float = Field(gt=0, le=180)
    manifest: Manifest
    manifest_hash: Hash
    recovery: Recovery | None
    provider_provenance: Literal[
        "AUTHOR-WRITTEN SCRIPTED RECORDINGS; synthetic tokens; $0 actual API spend"
    ]

    @field_validator("wall_seconds")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("non-finite latency")
        return value


class Aggregate(StrictModel):
    passed: bool
    scenario_outcomes_passed: int = Field(ge=0, le=10)
    mandatory_cases: Literal[10]
    published_reports: int = Field(ge=0, le=10)
    actual_api_spend_usd: Literal[0]
    wall_seconds: float = Field(gt=0, le=1800)


class CodeIdentity(StrictModel):
    git_sha: str | None = Field(pattern=r"^[a-f0-9]{40}$")
    package_version: str = Field(min_length=1)
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class Benchmark(StrictModel):
    schema_version: Literal[1]
    evaluation: Literal["deterministic systems outcomes; not model accuracy"]
    usage: Literal["labeled synthetic tokens; author-written scripted recordings"]
    code: CodeIdentity
    cases: list[CaseResult] = Field(min_length=10, max_length=10)
    aggregate: Aggregate


def validate_benchmark(value: Any, golden: dict[str, Any]) -> Benchmark:
    artifact = Benchmark.model_validate(value)
    scenarios = [case.scenario for case in artifact.cases]
    if len(set(scenarios)) != 10 or set(scenarios) != set(golden["cases"]):
        raise ValueError("missing/duplicate/unknown mandatory golden cases")
    for case in artifact.cases:
        expected = golden["cases"][case.scenario]
        if case.expected != expected or case.manifest_hash != object_hash(
            case.manifest.model_dump()
        ):
            raise ValueError("golden expectations or manifest hash mismatch")
        if (
            case.manifest.source_fingerprint != artifact.code.source_fingerprint
            or case.manifest.package_version != artifact.code.package_version
        ):
            raise ValueError("case code identity differs from benchmark code")
        if not case.passed or case.failures:
            raise ValueError("mandatory scenario failed")
        if expected.get("rejected"):
            if (
                case.observed_report is not None
                or case.status != "failed"
                or case.verdict is None
                or case.verdict.accepted
            ):
                raise ValueError("rejection outcome was not demonstrated")
        else:
            if (
                case.observed_report is None
                or case.status != "completed"
                or case.verdict is None
                or not case.verdict.accepted
            ):
                raise ValueError("successful publication outcome missing")
            facts = {fact.citation.field: fact.value for fact in case.observed_report.facts}
            for name in ("total", "orders"):
                if name not in facts or abs(facts[name] - expected[name]) > 1e-6:
                    raise ValueError(f"independent golden mismatch for {case.scenario}.{name}")
            chart = {point.label: point.value for point in case.observed_report.chart}
            if chart != expected.get("chart", {}):
                raise ValueError("golden chart labels/values mismatch")
            if expected.get("no_data") and not case.observed_report.no_data:
                raise ValueError("no-data outcome missing")
        if case.scenario == "interruption":
            if case.recovery is None or case.recovery.barrier.run_id != case.run_id:
                raise ValueError("interruption process-kill provenance missing")
        elif case.recovery is not None:
            raise ValueError("unexpected recovery fixture")
    aggregate = artifact.aggregate
    if (
        not aggregate.passed
        or aggregate.scenario_outcomes_passed != 10
        or aggregate.published_reports != sum(c.observed_report is not None for c in artifact.cases)
        or not math.isfinite(aggregate.wall_seconds)
        or abs(aggregate.wall_seconds - sum(c.wall_seconds for c in artifact.cases)) > 1e-6
    ):
        raise ValueError("aggregate disagrees with actual case metrics/outcomes")
    return artifact


def git_revision(directory: Path) -> str | None:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None
