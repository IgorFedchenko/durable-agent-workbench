"""Strict rubric/provenance pipeline. A judge cannot override deterministic gates."""

from __future__ import annotations

import json
from typing import Any, Literal

from .engine import Engine
from .models import JudgeResult, RunConfig, StrictModel
from .store import BoundaryError
from .util import ROOT, file_hash, object_hash


class JudgedArtifact(StrictModel):
    schema_version: Literal[1] = 1
    judged_run_id: str
    report_hash: str
    dataset_hash: str
    rubric_hash: str
    prompt_hash: str
    decision_hash: str
    result: JudgeResult
    rubric_passed: bool
    scope: Literal["rubric pipeline only", "contract-tested/live-unverified"]


def validate_judgment(
    engine: Engine,
    run_id: str,
    result: JudgeResult,
    *,
    expected_report_hash: str,
    expected_rubric_hash: str,
) -> dict[str, Any]:
    snapshot = engine.snapshot(run_id)
    if snapshot["report"] is None:
        raise BoundaryError("judge requires a published report")
    prompt_hash = file_hash(ROOT / "prompts/judge-v1.txt")
    rubric_hash = file_hash(ROOT / "evals/rubric.json")
    report_hash = object_hash(snapshot["report"])
    if (
        result.prompt_hash != prompt_hash
        or expected_report_hash != report_hash
        or expected_rubric_hash != rubric_hash
    ):
        raise BoundaryError("judge prompt/rubric/report provenance mismatch")
    if result.provenance == "author-written-scripted" and result.model != "author-written-judge-v1":
        raise BoundaryError("offline judge cannot masquerade as an executed model")
    envelope = JudgedArtifact(
        judged_run_id=run_id,
        report_hash=report_hash,
        dataset_hash=snapshot["run"]["manifest"]["dataset_hash"],
        rubric_hash=rubric_hash,
        prompt_hash=prompt_hash,
        decision_hash=object_hash(result.model_dump()),
        result=result,
        rubric_passed=min(result.correctness, result.evidence, result.clarity, result.safety) >= 3,
        scope="rubric pipeline only"
        if result.provenance == "author-written-scripted"
        else "contract-tested/live-unverified",
    )
    engine.store.event(run_id, "judge_result", envelope.model_dump())
    return envelope.model_dump()


def judge_report(engine: Engine, run_id: str) -> dict[str, Any]:
    fixture = json.loads((ROOT / "evals/judge-authored.json").read_text())
    fixture["prompt_hash"] = file_hash(ROOT / "prompts/judge-v1.txt")
    return validate_judgment(
        engine,
        run_id,
        JudgeResult.model_validate(fixture),
        expected_report_hash=object_hash(engine.snapshot(run_id)["report"]),
        expected_rubric_hash=file_hash(ROOT / "evals/rubric.json"),
    )


def live_judge(engine: Engine, run_id: str, config: RunConfig) -> dict[str, Any]:
    if config.mode == "scripted" or not config.allow_live:
        raise BoundaryError("live judge requires explicit priced provider opt-in")
    snapshot = engine.snapshot(run_id)
    if snapshot["report"] is None:
        raise BoundaryError("judge requires a published report")
    # A separate root ledger freezes this manual judge's prices and bounds.
    judge_id = engine.create("Evaluate the report using the fixed judge rubric", config)
    prompt_hash = file_hash(ROOT / "prompts/judge-v1.txt")
    with engine.store.ownership(judge_id):
        provider = engine.provider(config)
        turn = engine._provider_turn(
            judge_id,
            judge_id,
            config,
            provider,
            (ROOT / "prompts/judge-v1.txt").read_text(),
            [
                {
                    "role": "user",
                    "content": {
                        "report": snapshot["report"],
                        "trust": "untrusted_report",
                        "prompt_hash": prompt_hash,
                        "rubric": json.loads((ROOT / "evals/rubric.json").read_text()),
                        "required_provenance": "live",
                        "model": config.model,
                    },
                }
            ],
            "judge",
            0,
        )
        if turn.decision.kind != "final" or not isinstance(turn.decision.answer, JudgeResult):
            raise BoundaryError("judge must return structured JudgeResult")
        if turn.decision.answer.provenance != "live" or turn.decision.answer.model != config.model:
            raise BoundaryError("live judge model/provenance mismatch")
        engine.store.save_decision(judge_id, 0, turn.model_dump())
        engine.store.status(judge_id, "completed")
        return validate_judgment(
            engine,
            run_id,
            turn.decision.answer,
            expected_report_hash=object_hash(snapshot["report"]),
            expected_rubric_hash=file_hash(ROOT / "evals/rubric.json"),
        )
