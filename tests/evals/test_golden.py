"""Ten real engine/Docker outcomes checked against literal independent arithmetic."""

from __future__ import annotations

import copy
import hashlib
import json
import time

import pytest

from agent_workbench.engine import Engine
from agent_workbench.evals import evaluate_case, kill_and_resume
from agent_workbench.models import RunConfig
from agent_workbench.providers.scripted import ScriptedProvider
from agent_workbench.store import BoundaryError
from agent_workbench.util import ROOT
from tests.helpers import SCENARIOS, assert_dataset, oracle

pytestmark = pytest.mark.docker


def assert_oracle(scenario, snapshot):
    if scenario == "reviewer-rejection":
        assert (
            snapshot["report"] is None
            and snapshot["verdict"]["accepted"] is False
            and snapshot["run"]["status"] == "failed"
        )
        return
    total, orders, chart = oracle(scenario)
    facts = {f["citation"]["field"]: f["value"] for f in snapshot["report"]["facts"]}
    assert facts["total"] == total and facts["orders"] == orders
    assert {p["label"]: p["value"] for p in snapshot["report"]["chart"]} == chart
    assert snapshot["verdict"]["accepted"]
    for fact in snapshot["report"]["facts"] + snapshot["report"]["chart"]:
        evidence = next(
            e for e in snapshot["evidence"] if e["id"] == fact["citation"]["evidence_id"]
        )
        assert (
            evidence["root_id"] == snapshot["run"]["id"]
            and evidence["dataset_hash"] == snapshot["run"]["manifest"]["dataset_hash"]
        )
        value = evidence["body"]
        for part in fact["citation"]["field"].split("."):
            value = value[part]
        assert abs(value - fact["value"]) <= 1e-6


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_golden_outcome(store, scenario):
    assert_dataset()
    engine = Engine(store)
    config = RunConfig(scenario=scenario)
    root = engine.create("Independent six-order arithmetic", config)
    started = time.monotonic()
    killed = False
    if scenario == "interruption":
        recovery = kill_and_resume(store.directory, root)
        killed = True
        assert recovery["worker_pid"] > 0 and recovery["exitcode"] == -9
    elif scenario == "reviewer-rejection":
        with pytest.raises(BoundaryError, match="verdict"):
            engine.resume(root)
    else:
        engine.resume(root)
    elapsed = time.monotonic() - started
    snap = engine.snapshot(root)
    assert_oracle(scenario, snap)
    assert (
        snap["metrics"]["steps"] <= 24
        and snap["metrics"]["charged_tokens"] <= 20000
        and elapsed <= 180
    )
    assert snap["metrics"]["actual_api_spend_usd"] == 0 and not snap["metrics"]["usage_unknown"]
    assert "AUTHOR-WRITTEN SCRIPTED RECORDINGS" in snap["provenance"]
    assert evaluate_case(scenario, snap, elapsed, killed_and_resumed=killed) == (True, [])
    image_events = [e for e in snap["events"] if e["kind"] == "sandbox_created"]
    assert image_events
    assert all(
        e["payload"]["image_id"] == snap["run"]["manifest"]["sandbox_image_id"]
        for e in image_events
    )
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM reports WHERE root_id=?", (root,)).fetchone()[
            0
        ] == (0 if scenario == "reviewer-rejection" else 1)
    if scenario == "repair":
        results = [
            e["payload"]
            for e in snap["events"]
            if e["kind"] == "tool_result" and e["payload"]["tool"] == "analyze"
        ]
        assert (
            len(results) == 2
            and results[0]["result"]["ok"] is False
            and results[1]["result"]["ok"] is True
            and results[0]["call_id"] != results[1]["call_id"]
        )
    if scenario == "compaction":
        summaries = [e["payload"] for e in snap["events"] if e["kind"] == "compaction"]
        assert summaries and all(s["trust"] == "untrusted_tool_output" for s in summaries)
        assert any(
            e["kind"] == "tool_result" and e["payload"].get("call_id") == "retrieve-early"
            for e in snap["events"]
        )
    assert (
        snap["run"]["manifest"]["dataset_hash"]
        == hashlib.sha256(engine.dataset(config).read_bytes()).hexdigest()
    )


def test_golden_rejects_consistently_wrong_analysis(store):
    class WrongAnalysis:
        def __init__(self):
            self.base = ScriptedProvider(ROOT / "recordings/total.json")

        def turn(self, system, messages, **kwargs):
            turn = self.base.turn(system, messages, **kwargs)
            decision = turn.decision
            if decision.kind == "tool" and decision.name == "analyze":
                decision = decision.model_copy(
                    update={
                        "arguments": {
                            "code": 'import json;print(json.dumps({"total":174,"orders":6}))'
                        }
                    }
                )
            if decision.kind == "tool" and decision.name == "publish":
                args = copy.deepcopy(decision.arguments)
                for fact in args["facts"]:
                    if fact["citation"]["field"] == "total":
                        fact["value"] = 174.0
                decision = decision.model_copy(update={"arguments": args})
            return turn.model_copy(update={"decision": decision})

    engine = Engine(store, provider=WrongAnalysis())
    root = engine.create("Independent negative control", RunConfig(scenario="total"))
    engine.resume(root)
    snap = engine.snapshot(root)
    assert (
        snap["report"]["facts"][0]["value"] == 174
        and snap["verdict"]["accepted"]
        and store.report(root)
    )
    with pytest.raises(AssertionError):
        assert_oracle("total", snap)
    passed, failures = evaluate_case("total", snap, 0.1)
    assert not passed and any("175" in f and "174" in f for f in failures)
    # This separate negative artifact never contributes to 10/10 baseline outcomes.
    (store.directory / "negative-control.json").write_text(
        json.dumps({"passed": passed, "failures": failures, "observed": snap["report"]})
    )
