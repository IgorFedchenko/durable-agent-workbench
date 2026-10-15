"""Ten deterministic systems outcomes; these are not live model accuracy metrics."""

from __future__ import annotations

import json
import multiprocessing
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .engine import SCENARIOS, Engine
from .models import RunConfig
from .store import BoundaryError, Store
from .util import ROOT, object_hash


def evaluate_case(
    scenario: str, snapshot: dict[str, Any], elapsed: float, *, killed_and_resumed: bool = False
) -> tuple[bool, list[str]]:
    spec = json.loads((ROOT / "evals" / "golden.json").read_text())["cases"][scenario]
    failures: list[str] = []
    report = snapshot["report"]
    if spec.get("rejected"):
        if report is not None or not snapshot["verdict"] or snapshot["verdict"]["accepted"]:
            failures.append("reviewer rejection must produce zero reports")
        if snapshot["run"]["status"] != "failed":
            failures.append("rejected publication must retain failed timeline")
    elif report is None:
        failures.append("missing successful report")
    else:
        facts = {f["citation"]["field"]: f["value"] for f in report["facts"]}
        for field in ("total", "orders"):
            if field not in facts or abs(facts[field] - spec[field]) > 1e-6:
                failures.append(f"{field}: expected {spec[field]}, observed {facts.get(field)}")
        if not snapshot["verdict"] or not snapshot["verdict"]["accepted"]:
            failures.append("missing accepting child verdict")
        if spec.get("chart") != (
            {p["label"]: p["value"] for p in report["chart"]} if report["chart"] else None
        ):
            failures.append("chart labels/values differ from frozen expectations")
        if spec.get("no_data") and not report["no_data"]:
            failures.append("missing explicit no-data label")
    events = snapshot["events"]
    if spec.get("analysis_failure") and not any(
        e["kind"] == "tool_result" and e["payload"].get("result", {}).get("ok") is False
        for e in events
    ):
        failures.append("deliberate failed analysis not retained")
    if spec.get("compaction") and not any(e["kind"] == "compaction" for e in events):
        failures.append("context did not compact")
    if spec.get("late_retrieval") and not any(
        e["kind"] == "tool_result" and e["payload"].get("call_id") == "retrieve-early"
        for e in events
    ):
        failures.append("early evidence was not retrieved after compaction")
    if spec.get("process_kill_resume") and not killed_and_resumed:
        failures.append("separate worker SIGKILL and resume not demonstrated")
    metrics = snapshot["metrics"]
    if metrics["steps"] > 24 or metrics["charged_tokens"] > 20000 or elapsed > 180:
        failures.append("step/token/wall ceiling exceeded")
    if metrics["actual_api_spend_usd"] != 0 or metrics["usage_unknown"]:
        failures.append("offline usage/spend provenance invalid")
    return not failures, failures


def _barrier_worker(directory: str, run_id: str, connection: Any) -> None:
    def checkpoint(name: str, payload: dict[str, Any]) -> None:
        if name == "call_completed" and payload.get("call_id") == "analysis-1":
            connection.send({"committed": True, "run_id": run_id})
            connection.recv()

    Engine(Store(Path(directory), checkpoint=checkpoint)).resume(run_id)


def kill_and_resume(directory: Path, run_id: str, timeout: float = 170) -> dict[str, Any]:
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    worker = ctx.Process(target=_barrier_worker, args=(str(directory), run_id, child))
    started = time.monotonic()
    worker.start()
    child.close()
    try:
        if not parent.poll(timeout / 2):
            raise BoundaryError("interruption worker failed to reach committed analysis barrier")
        barrier = parent.recv()
        worker.kill()
        worker.join(timeout=5)
        if worker.exitcode != -signal.SIGKILL:
            raise BoundaryError("interruption fixture did not kill a separate process")
        before = Store(directory).events(run_id)
        launches = sum(e["kind"] == "sandbox_created" for e in before)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_workbench.cli",
                "--data",
                str(directory),
                "resume",
                run_id,
            ],
            capture_output=True,
            timeout=max(1, timeout - (time.monotonic() - started)),
            check=False,
        )
        if result.returncode:
            raise BoundaryError("replacement worker failed during interruption scenario")
        after = Store(directory).events(run_id)
        if sum(e["kind"] == "sandbox_created" for e in after) != launches:
            raise BoundaryError("completed analysis reran after process death")
        return {
            "worker_pid": worker.pid,
            "exitcode": worker.exitcode,
            "replacement_exitcode": result.returncode,
            "barrier": barrier,
            "analysis_launches_before": launches,
            "analysis_launches_after": launches,
        }
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=5)
        parent.close()


def run_benchmark(directory: Path, output: Path) -> dict[str, Any]:
    store = Store(directory)
    engine = Engine(store)
    cases: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        started = time.monotonic()
        run_id = engine.create(
            "Analyze the synthetic retail fixture and cite evidence.", RunConfig(scenario=scenario)
        )
        recovery: dict[str, Any] | None = None
        error: str | None = None
        try:
            if scenario == "interruption":
                recovery = kill_and_resume(directory.resolve(), run_id)
            else:
                engine.resume(run_id)
        except BoundaryError as exc:
            error = str(exc)
            if scenario != "reviewer-rejection":
                store.status(run_id, "failed", error)
        elapsed = time.monotonic() - started
        snapshot = engine.snapshot(run_id)
        passed, failures = evaluate_case(
            scenario, snapshot, elapsed, killed_and_resumed=recovery is not None
        )
        if error and scenario != "reviewer-rejection":
            failures.append(error)
            passed = False
        manifest = snapshot["run"]["manifest"]
        cases.append(
            {
                "scenario": scenario,
                "passed": passed,
                "failures": failures,
                "run_id": run_id,
                "expected": json.loads((ROOT / "evals/golden.json").read_text())["cases"][scenario],
                "observed_report": snapshot["report"],
                "metrics": snapshot["metrics"],
                "wall_seconds": elapsed,
                "manifest": manifest,
                "manifest_hash": object_hash(manifest),
                "recovery": recovery,
                "provider_provenance": snapshot["provenance"],
            }
        )
    result = {
        "schema_version": 1,
        "evaluation": "deterministic systems outcomes; not model accuracy",
        "usage": "labeled synthetic tokens; author-written scripted recordings",
        "cases": cases,
        "aggregate": {
            "passed": len(cases) == 10 and all(c["passed"] for c in cases),
            "scenario_outcomes_passed": sum(c["passed"] for c in cases),
            "mandatory_cases": 10,
            "published_reports": sum(c["observed_report"] is not None for c in cases),
            "actual_api_spend_usd": 0,
            "wall_seconds": sum(c["wall_seconds"] for c in cases),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result
