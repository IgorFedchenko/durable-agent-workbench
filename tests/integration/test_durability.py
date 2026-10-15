"""SQLite and real-process crash contracts; Docker engine tests stay real."""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import signal
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_workbench.engine import Engine, IncompatibleResume
from agent_workbench.models import Final, Limits, RunConfig, ToolCall, Turn, Usage, Verdict
from agent_workbench.store import BoundaryError, BusyRun, Store
from agent_workbench.util import ROOT
from tests.helpers import Worker, paid_limits, publication, raw_run, report


def _lock_owner(directory, root, connection):
    store = Store(Path(directory))
    try:
        with store.ownership(root):
            helper = subprocess.Popen(
                [sys.executable, "-c", "import time;time.sleep(30)"], close_fds=False
            )
            connection.send(
                {
                    "owned": True,
                    "inode": os.stat(store.directory / f"{root}.lock").st_ino,
                    "pid": os.getpid(),
                    "helper_pid": helper.pid,
                }
            )
            connection.recv()
    except BusyRun:
        connection.send({"busy": True})
    finally:
        connection.close()


def _reservation_worker(directory, root, run, limits, barrier, connection):
    store = Store(Path(directory))
    barrier.wait()
    try:
        connection.send(
            {"reservation": store.reserve(root, run, Limits.model_validate(limits), True)}
        )
    except BoundaryError:
        connection.send({"refused": True})
    finally:
        connection.close()


def _ledger_crash(directory, root, limits, dispatched, connection):
    store = Store(Path(directory))
    limits = Limits.model_validate(limits)
    with store.ownership(root):
        rid = store.reserve(root, root, limits, True)
        if dispatched:
            store.dispatched(rid)
        connection.send(rid)
        connection.recv()


def _publish_worker(directory, root, eid, barrier, connection):
    def checkpoint(name, payload):
        if name == barrier:
            connection.send(name)
            connection.recv()

    Store(Path(directory), checkpoint=checkpoint).publish(root, "publication", report(eid))


def _spawn(target, *args):
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=target, args=(*args, child))
    proc.start()
    child.close()
    return proc, parent


def _kill(proc):
    proc.kill()
    proc.join(5)
    assert proc.exitcode == -signal.SIGKILL


def _resume(directory, root):
    completed = subprocess.run(
        [sys.executable, "-m", "agent_workbench.cli", "--data", str(directory), "resume", root],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, completed.stderr


def _real(store, scenario="total"):
    engine = Engine(store)
    root = engine.create("Independent literal fixture", RunConfig(scenario=scenario))
    return engine, root


def test_root_lifetime_flock_contention(store):
    # The engine's ownership checkpoint is before any manifest/provider/container work.
    engine, root = _real(store)
    owner = Worker(store.directory, root, "root_owned")
    try:
        owner.reached()
        before = store.events(root)
        contender = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_workbench.cli",
                "--data",
                str(store.directory),
                "resume",
                root,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert contender.returncode != 0 and "live owner" in (contender.stdout + contender.stderr)
        assert store.events(root) == before and not store.ledger(root)
        other = raw_run(store)
        with store.ownership(other):
            pass
        assert owner.process.is_alive()
    finally:
        owner.close()


def test_lock_inode_stable_and_not_inherited(store):
    root = raw_run(store)
    proc, pipe = _spawn(_lock_owner, str(store.directory), root)
    try:
        assert pipe.poll(15)
        first = pipe.recv()
        inode = first["inode"]
        for _ in range(3):
            with pytest.raises(BusyRun):
                with store.ownership(root):
                    pass
            assert (store.directory / f"{root}.lock").stat().st_ino == inode
        _kill(proc)
        os.kill(first["helper_pid"], 0)
        with store.ownership(root):
            fd = os.open(store.directory / f"{root}.lock", os.O_RDONLY)
            assert not os.get_inheritable(fd)
            os.close(fd)
        assert (store.directory / f"{root}.lock").stat().st_ino == inode
    finally:
        if proc.is_alive():
            _kill(proc)
        if "first" in locals():
            os.kill(first["helper_pid"], signal.SIGTERM)
        pipe.close()


def test_publication_evidence_binding(store):
    root, eid, _ = publication(store)
    with pytest.raises(BoundaryError, match="differs"):
        store.publish(root, "publication", report(eid, 174))
    assert store.report(root) is None
    store.publish(root, "publication", report(eid))
    assert store.report(root)["facts"][0]["value"] == 175


def test_uncited_chart_point_rejected(store):
    root, eid, _ = publication(store)
    for label, value, field in [
        ("north", 100, "regions.north"),
        ("south", 75, "regions.north"),
        ("north", 75, "missing"),
        ("north", 75, "regions.0"),
    ]:
        point = {
            "label": label,
            "value": float(value),
            "citation": {"evidence_id": eid, "field": field},
        }
        with pytest.raises(BoundaryError):
            store.publish(root, "pub", report(eid, chart=[point]))
    assert store.report(root) is None


def test_foreign_or_missing_evidence_rejected(store):
    root, eid, _ = publication(store)
    foreign, other, _ = publication(store)
    for reference in ["ev-nonexistent", other]:
        with pytest.raises(BoundaryError):
            store.publish(root, "pub", report(reference))
    assert store.report(root) is None
    with pytest.raises(BoundaryError):
        store.evidence(foreign, eid)
    with pytest.raises(BoundaryError):
        store.add_evidence(root, "analysis", {"total": 174})
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as db:
            db.execute("UPDATE evidence SET body=? WHERE id=?", ("{}", eid))


def test_missing_or_rejected_verdict_rejected(store):
    root = raw_run(store)
    eid = store.add_evidence(root, "analysis", {"total": 175})
    with pytest.raises(BoundaryError, match="verdict"):
        store.publish(root, "pub", report(eid))
    root, eid, _ = publication(store, accepted=False)
    with pytest.raises(BoundaryError, match="verdict"):
        store.publish(root, "pub", report(eid))
    assert store.report(root) is None


def test_evidence_root_dataset_verdict_binding(store):
    root, eid, child = publication(store)
    other = raw_run(store)
    with pytest.raises(BoundaryError):
        store.save_verdict(
            other, child, Verdict(accepted=True, evidence_ids=[eid], reason="forged")
        )
    with pytest.raises(BoundaryError):
        store.publish(child, "pub", report(eid))
    with pytest.raises(BoundaryError):
        store.save_verdict(
            root, child, Verdict(accepted=False, evidence_ids=[eid], reason="replacement")
        )
    engine = Engine(store)
    for name, args in [
        ("publish", report(eid).model_dump()),
        ("review", {"evidence_id": eid}),
        ("analyze", {"code": "print(1)"}),
    ]:
        with pytest.raises(BoundaryError, match="capability"):
            engine._tool(
                child,
                RunConfig(),
                engine.provider(RunConfig()),
                ToolCall(id="evil", name=name, arguments=args),
                "reviewer",
            )


def test_publication_numeric_fields_complete(store):
    for value in [0.0, -1.0, 175.0, 1e12]:
        root, eid, _ = publication(store, {"total": value})
        store.publish(root, "within", report(eid, value + (0.0000005 if value != 1e12 else 0)))
        root, eid, _ = publication(store, {"total": value})
        with pytest.raises(BoundaryError):
            store.publish(root, "outside", report(eid, value + 0.01))
    for evidence_value in [True, "175", {"nested": 175}, [175]]:
        root, eid, _ = publication(store, {"total": evidence_value})
        with pytest.raises(BoundaryError):
            store.publish(root, "pub", report(eid))
    for value in [float("nan"), float("inf"), -float("inf"), 1e16]:
        with pytest.raises(ValidationError):
            report("ev-one", value)


def test_publication_repeated_and_contended(store):
    root, eid, _ = publication(store)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: store.publish(root, "pub", report(eid)), range(4)))
    assert results == [{"published": True, "report_key": root}] * 4
    with store.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM reports WHERE root_id=?", (root,)).fetchone()[0] == 1
        )
    with pytest.raises(BoundaryError):
        store.publish(root, "pub", report(eid, 174))


def test_publication_crash_atomicity(store):
    for barrier in [
        "publication_before_transaction",
        "publication_uncommitted",
        "publication_committed",
    ]:
        root, eid, _ = publication(store)
        proc, pipe = _spawn(_publish_worker, str(store.directory), root, eid, barrier)
        try:
            assert pipe.poll(15)
            assert pipe.recv() == barrier
            assert (store.report(root) is not None) == (barrier == "publication_committed")
            _kill(proc)
            replacement, other = _spawn(_publish_worker, str(store.directory), root, eid, "never")
            replacement.join(15)
            assert replacement.exitcode == 0
            other.close()
            assert store.report(root)["facts"][0]["value"] == 175
            with store.connect() as db:
                assert (
                    db.execute("SELECT count(*) FROM reports WHERE root_id=?", (root,)).fetchone()[
                        0
                    ]
                    == 1
                )
                assert (
                    db.execute(
                        "SELECT state FROM calls WHERE run_id=? AND call_id=?",
                        (root, "publication"),
                    ).fetchone()[0]
                    == "completed"
                )
        finally:
            if proc.is_alive():
                _kill(proc)
            pipe.close()


def test_budget_parent_child_atomic_race(store):
    for ceiling in ["cost", "tokens", "steps"]:
        kwargs = {
            "max_cost_usd": 0.05 if ceiling == "cost" else 1.0,
            "max_tokens": 20 if ceiling == "tokens" else 1000,
            "max_steps": 1 if ceiling == "steps" else 24,
        }
        limits = Limits(
            input_token_cap=10,
            output_token_cap=10,
            input_price_per_million=1000.0,
            output_price_per_million=2000.0,
            **kwargs,
        )
        root = raw_run(store, live=True, limits=limits)
        child = raw_run(store, live=True, limits=limits, parent=root)
        ctx = mp.get_context("spawn")
        barrier = ctx.Barrier(2)
        workers = []
        with store.ownership(root):
            for run in [root, child]:
                workers.append(
                    _spawn(
                        _reservation_worker,
                        str(store.directory),
                        root,
                        run,
                        limits.model_dump(),
                        barrier,
                    )
                )
            results = []
            for proc, pipe in workers:
                assert pipe.poll(15)
                results.append(pipe.recv())
                proc.join(5)
                assert proc.exitcode == 0
                pipe.close()
        assert (
            sum("reservation" in r for r in results) == 1
            and sum("refused" in r for r in results) == 1
        )
        assert len(store.ledger(root)) == 1 and store.ledger(root)[0]["charged_cost"] == 0.03


def _ledger_kill_test(store, dispatched):
    limits = paid_limits()
    root = raw_run(store, live=True, limits=limits)
    proc, pipe = _spawn(_ledger_crash, str(store.directory), root, limits.model_dump(), dispatched)
    try:
        assert pipe.poll(15)
        rid = pipe.recv()
        _kill(proc)
        replacement = Store(store.directory)
        with replacement.ownership(root):
            replacement.mark_ambiguous(root)
        row = replacement.ledger(root)[0]
        assert (
            row["id"] == rid
            and row["charged_tokens"] == 20
            and row["charged_cost"] == 0.03
            and row["actual_usage"] is None
            and row["state"] == "ambiguous"
        )
        with pytest.raises(BoundaryError):
            replacement.reserve(root, root, limits, True)
    finally:
        if proc.is_alive():
            _kill(proc)
        pipe.close()


def test_kill_after_reservation_retains_charge(store):
    _ledger_kill_test(store, False)


def test_kill_after_dispatch_unknown_outcome(store):
    _ledger_kill_test(store, True)


def test_known_usage_settlement_and_retry(store):
    limits = Limits(**{**paid_limits().model_dump(), "max_cost_usd": 0.1})
    root = raw_run(store, live=True, limits=limits)
    rid = store.reserve(root, root, limits, True)
    store.dispatched(rid)
    usage = Usage(input_tokens=6, output_tokens=4)
    store.settle(rid, usage, limits)
    store.settle(rid, usage, limits)
    assert (
        store.ledger(root)[0]["charged_tokens"] == 10
        and store.ledger(root)[0]["charged_cost"] == 0.014
    )
    second = store.reserve(root, root, limits, True)
    store.settle(second, None, limits)
    store.reserve(root, root, limits, True)
    assert len(store.ledger(root)) == 3 and sum(
        r["charged_cost"] for r in store.ledger(root)
    ) == pytest.approx(0.074)
    with pytest.raises(BoundaryError):
        store.settle(rid, Usage(input_tokens=1, output_tokens=1), limits)


def test_missing_usage_is_conservative(store):
    limits = paid_limits()
    root = raw_run(store, live=True, limits=limits)
    rid = store.reserve(root, root, limits, True)
    store.settle(rid, None, limits)
    row = store.ledger(root)[0]
    assert (
        row["state"] == "ambiguous" and row["actual_usage"] is None and row["charged_cost"] == 0.03
    )
    snap = Engine(store).snapshot(root)
    assert snap["metrics"]["usage_unknown"] and snap["metrics"]["actual_api_spend_usd"] is None
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1, output_tokens=1)


def test_underestimated_actual_usage_fails_safely(store):
    limits = paid_limits()
    root = raw_run(store, live=True, limits=limits)
    rid = store.reserve(root, root, limits, True)
    with pytest.raises(BoundaryError, match="exceeded"):
        store.settle(rid, Usage(input_tokens=20, output_tokens=20), limits)
    row = store.ledger(root)[0]
    assert row["state"] == "overrun" and row["charged_tokens"] == 40 and row["charged_cost"] == 0.06
    with pytest.raises(BoundaryError, match="prior"):
        store.reserve(root, root, limits, True)


def test_missing_price_model_output_cap_fails_closed(store):
    engine = Engine(store)
    for changes in [
        {"allow_live": False},
        {"model": "author-written-v1"},
        {"limits": Limits()},
        {"limits": Limits(input_price_per_million=1.0)},
        {"limits": Limits(output_price_per_million=1.0)},
    ]:
        config = RunConfig(
            mode="openai", model="fixture", allow_live=True, limits=paid_limits()
        ).model_copy(update=changes)
        with pytest.raises(BoundaryError):
            engine.create("Question", config)
    for changes in [{"output_token_cap": 0}, {"input_token_cap": 0}, {"max_tokens": -1}]:
        with pytest.raises(ValidationError):
            Limits(**changes)
    assert not store.ledger("not-a-root")


def test_reservation_precedes_transport(store):
    # Check inside the actual provider invocation, through Engine._provider_turn.
    config = RunConfig(
        mode="openai",
        model="fixture",
        allow_live=True,
        limits=Limits(
            input_token_cap=10000,
            output_token_cap=10,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
        ),
    )
    root = store.create_run("q", {"dataset_hash": "d" * 64}, config.model_dump())
    sends = []

    class FakePaid:
        def turn(self, *args, **kwargs):
            rows = Store(store.directory).ledger(root)
            assert (
                len(rows) == 1
                and rows[0]["state"] == "dispatched"
                and rows[0]["actual_usage"] is None
            )
            assert rows[0]["root_id"] == root and rows[0]["charged_tokens"] == 10010
            sends.append(rows[0]["id"])
            return Turn(
                decision=ToolCall(id="one", name="inspect_dataset", arguments={}),
                usage=Usage(input_tokens=6, output_tokens=4),
            )

    engine = Engine(store)
    with store.ownership(root):
        engine._provider_turn(
            root,
            root,
            config,
            FakePaid(),
            "policy",
            [{"role": "user", "content": "q"}],
            "parent",
            0,
        )
    assert len(sends) == 1 and store.ledger(root)[0]["state"] == "settled"


def test_budget_no_reset_with_compaction_child_or_restart(store):
    limits = Limits(input_token_cap=10, output_token_cap=10, max_steps=3, max_tokens=100)
    root = raw_run(store, limits=limits)
    child = raw_run(store, limits=limits, parent=root)
    first = store.reserve(root, root, limits, False)
    store.settle(first, Usage(input_tokens=6, output_tokens=4, synthetic=True), limits)
    store.tool_step(root, child, "read")
    restarted = Store(store.directory)
    restarted.reserve(root, child, limits, False)
    assert restarted.step_count(root) == 3
    with pytest.raises(BoundaryError):
        restarted.reserve(root, root, limits, False)
    with pytest.raises(BoundaryError):
        restarted.tool_step(root, root, "extra")


def test_budget_preserved_across_resume(store):
    limits = paid_limits()
    root = raw_run(store, live=True, limits=limits)
    store.reserve(root, root, limits, True)
    for changes in [
        {"input_price_per_million": 1.0},
        {"max_cost_usd": 1.0},
        {"max_tokens": 100000},
    ]:
        changed = limits.model_copy(update=changes)
        with pytest.raises(BoundaryError, match="immutable"):
            Store(store.directory).reserve(root, root, changed, True)
    assert store.ledger(root)[0]["charged_cost"] == 0.03


def test_retry_identity_and_attempts(store):
    root = raw_run(store)
    args = {"code": "x"}
    assert store.begin_call(root, "same", "analyze", args) is None
    assert [store.tool_step(root, root, "same") for _ in range(3)] == [1, 2, 3]
    with pytest.raises(BoundaryError):
        store.tool_step(root, root, "same")
    for name, changed in [("analyze", {"code": "y"}), ("inspect_dataset", args)]:
        with pytest.raises(BoundaryError):
            store.begin_call(root, "same", name, changed)
    store.finish_call(root, "same", {"ok": False})
    assert store.begin_call(root, "same", "analyze", args) == {"ok": False}
    assert store.begin_call(root, "corrected", "analyze", {"code": "y"}) is None


def test_read_only_replay_has_no_execution(store):
    engine, root = _real(store)
    engine.resume(root)
    before = (store.events(root), store.ledger(root), store.step_count(root))
    for _ in range(3):
        snap = engine.snapshot(root)
        assert snap["report"] is not None
    assert (store.events(root), store.ledger(root), store.step_count(root)) == before


def test_resume_manifest_compatibility(store, monkeypatch):
    engine, root = _real(store)
    frozen = store.run(root)["manifest"]
    original = engine.manifest
    for field in frozen:
        changed = {**frozen, field: "incompatible-value"}
        monkeypatch.setattr(engine, "manifest", lambda config, changed=changed: changed)
        with pytest.raises(IncompatibleResume, match=field):
            engine.resume(root)
        assert not store.ledger(root) and not store.containers(root)
    monkeypatch.setattr(engine, "manifest", original)
    engine.resume(root)
    assert store.report(root)


def test_visible_preflight_failure_and_busy_owner(store, monkeypatch):
    engine, root = _real(store)
    frozen = store.run(root)["manifest"]
    monkeypatch.setattr(
        engine, "manifest", lambda config: {**frozen, "sandbox_image_id": "changed"}
    )
    with pytest.raises(IncompatibleResume):
        engine.resume(root)
    assert "sandbox_image_id" in store.run(root)["error"] and "new run" in store.run(root)["error"]
    before = store.run(root)
    proc, pipe = _spawn(_lock_owner, str(store.directory), root)
    try:
        assert pipe.poll(15)
        pipe.recv()
        with pytest.raises(BusyRun):
            engine.resume(root)
        assert store.run(root) == before
    finally:
        if proc.is_alive():
            _kill(proc)
        pipe.close()


def test_completed_call_reused_after_restart(store):
    engine, root = _real(store)
    worker = Worker(store.directory, root, "call_completed", call_id="analysis-1")
    try:
        worker.reached()
        launches = sum(e["kind"] == "sandbox_created" for e in store.events(root))
        worker.kill()
        _resume(store.directory, root)
        assert (
            launches == 1 and sum(e["kind"] == "sandbox_created" for e in store.events(root)) == 1
        )
        assert store.report(root)["facts"][0]["value"] == 175
    finally:
        worker.close()


def test_kill_before_dispatch(store):
    engine = Engine(store)
    # This recovery fixture permits the retained ambiguous reservation plus a fresh one.
    # Dedicated paid-ledger crash fixtures separately require refusal at a tight ceiling.
    root = engine.create(
        "Recovery before transport dispatch",
        RunConfig(scenario="total", limits=Limits(max_tokens=40000)),
    )
    worker = Worker(store.directory, root, "provider_reserved")
    try:
        worker.reached()
        assert not any(e["kind"] == "provider_dispatched" for e in store.events(root))
        worker.kill()
        before = store.ledger(root)[0]
        _resume(store.directory, root)
        after = store.ledger(root)[0]
        assert (
            after["charged_tokens"] == before["charged_tokens"]
            and after["state"] == "ambiguous"
            and after["actual_usage"] is None
        )
        assert store.report(root)
    finally:
        worker.close()


def test_preintent_staging_kill_cleanup(store):
    for barrier in ["staging_allocated", "staging_prepared_before_intent"]:
        engine, root = _real(store)
        sibling = raw_run(store)
        sibling_dir = engine.sandbox.staging_namespace(sibling)
        sibling_dir.mkdir(parents=True)
        sentinel = sibling_dir / "sentinel"
        sentinel.write_text("sibling survives")
        worker = Worker(store.directory, root, barrier)
        try:
            message = worker.reached()
            allocated = Path(message["payload"]["staging"])
            assert allocated.exists() and not store.containers(root)
            worker.kill()
            _resume(store.directory, root)
            assert (
                not engine.sandbox.staging_namespace(root).exists()
                and sentinel.read_text() == "sibling survives"
            )
        finally:
            worker.close()


def test_kill_after_tool_launch_cleans_owned_container(store):
    engine, root = _real(store)
    sibling = engine.create("Independent live sibling", RunConfig(scenario="total"))
    sibling_name = "daw-test-sibling-" + sibling
    image = store.run(sibling)["manifest"]["sandbox_image_id"]
    launched = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            sibling_name,
            "--label",
            "workbench.root=" + sibling,
            "--network",
            "none",
            "--read-only",
            "--user",
            "65532:65532",
            "--memory",
            "64m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "16",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import time;time.sleep(90)",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert launched.returncode == 0, launched.stderr
    worker = Worker(store.directory, root, "container_start_dispatched")
    try:
        message = worker.reached()
        name = message["payload"]["name"]
        worker.kill()
        _resume(store.directory, root)
        result = subprocess.run(["docker", "inspect", name], capture_output=True)
        assert result.returncode != 0 and not store.containers(root) and store.report(root)
        sibling_state = subprocess.run(
            ["docker", "inspect", sibling_name, "--format", "{{.State.Running}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert sibling_state.returncode == 0 and sibling_state.stdout.strip() == "true"
    finally:
        worker.close()
        subprocess.run(["docker", "rm", "-f", sibling_name], capture_output=True, timeout=20)


def test_structured_final_call_collision(store):
    root, eid, _ = publication(store)
    store.begin_call(root, "final-answer", "inspect_dataset", {})
    store.finish_call(root, "final-answer", {"rows": 6})
    with pytest.raises(BoundaryError, match="collides"):
        store.publish(root, "final-answer", report(eid))
    assert (
        store.begin_call(root, "final-answer", "inspect_dataset", {}) == {"rows": 6}
        and store.report(root) is None
    )


def test_logs_traces_correlation(store, caplog):
    caplog.set_level(logging.INFO, logger="workbench")
    engine, root = _real(store, "repair")
    engine.resume(root)
    engine.tracer_provider.force_flush()
    spans = [
        json.loads(line) for line in (store.directory / "traces.jsonl").read_text().splitlines()
    ]
    assert {"run", "provider", "tool", "reviewer_child"} <= {s["name"] for s in spans}
    rootspan = next(s for s in spans if s["name"] == "run")
    assert all(s["context"]["trace_id"] == rootspan["context"]["trace_id"] for s in spans)
    providers = [s for s in spans if s["name"] == "provider"]
    assert all(s["attributes"]["usage.synthetic"] for s in providers)
    assert all(s["attributes"]["latency.seconds"] >= 0 for s in providers)
    logs = [json.loads(r.message) for r in caplog.records if r.name == "workbench"]
    assert logs and all("run_id" in row and "trace_id" in row and "span_id" in row for row in logs)


def test_logs_otel_redaction(store, caplog):
    from agent_workbench.telemetry import log_event, safe_span

    caplog.set_level(logging.INFO, logger="workbench")
    engine = Engine(store)
    canary = "SYNTHETIC_PRIVATE_BODY_CANARY_78123"
    for name in ["run", "provider", "tool", "reviewer_child"]:
        with pytest.raises(RuntimeError):
            with safe_span(engine.tracer, name, attributes={"run.id": "synthetic"}):
                raise RuntimeError(canary)
    log_event(
        "failure",
        run_id="synthetic",
        category="RuntimeError",
        prompt=canary,
        authorization=canary,
        exception=canary,
    )
    engine.tracer_provider.force_flush()
    assert (
        canary not in (store.directory / "traces.jsonl").read_text() and canary not in caplog.text
    )
    assert "RuntimeError" in (store.directory / "traces.jsonl").read_text()


def test_telemetry_default_local_only(store, monkeypatch):
    import socket

    from agent_workbench.telemetry import safe_span, setup

    calls = []

    def blocked(*args, **kwargs):
        calls.append(args)
        raise AssertionError("external telemetry prohibited")

    monkeypatch.setattr(socket, "create_connection", blocked)
    provider = setup(store.directory / "local.jsonl")
    tracer = provider.get_tracer("fixture")
    with safe_span(tracer, "local", attributes={"run.id": "fixture"}):
        pass
    provider.shutdown()
    assert not calls and (store.directory / "local.jsonl").exists()
    for endpoint in ["https://127.0.0.1/v1/traces", "http://example.invalid/v1/traces"]:
        with pytest.raises(ValueError):
            setup(store.directory / "denied.jsonl", endpoint)


def test_structured_final_collision_through_engine(store):
    from agent_workbench.providers.scripted import ScriptedProvider

    base = ScriptedProvider(ROOT / "recordings/total.json")

    class CollisionProvider:
        def turn(self, system, messages, **kwargs):
            turn = base.turn(system, messages, **kwargs)
            decision = turn.decision
            if kwargs["role"] == "parent" and kwargs["turn"] == 0:
                decision = decision.model_copy(update={"id": "final-answer"})
            if decision.kind == "tool" and decision.name == "publish":
                from agent_workbench.models import Report

                decision = Final(answer=Report.model_validate(decision.arguments))
            return turn.model_copy(update={"decision": decision})

    engine = Engine(store, provider=CollisionProvider())
    root = engine.create("Structured Final collision", RunConfig(scenario="total"))
    with pytest.raises(BoundaryError, match="collides"):
        engine.resume(root)
    with store.connect() as db:
        row = db.execute(
            "SELECT name,state,result FROM calls WHERE run_id=? AND call_id=?",
            (root, "final-answer"),
        ).fetchone()
    assert (
        row["name"] == "inspect_dataset"
        and row["state"] == "completed"
        and json.loads(row["result"])["row_count"] == 6
    )
    assert store.report(root) is None


def test_shared_admission_prevents_every_transport(store, monkeypatch):
    from agent_workbench.judge import live_judge

    config = RunConfig(
        mode="openai",
        model="fixture-model",
        allow_live=True,
        limits=Limits(input_token_cap=1, input_price_per_million=1.0, output_price_per_million=2.0),
    )
    engine = Engine(store)
    calls = []

    class ForbiddenTransport:
        def turn(self, *args, **kwargs):
            calls.append(kwargs)
            raise AssertionError("inadmissible transport send")

    root = store.create_run("q", {"dataset_hash": "d" * 64}, config.model_dump())
    child = store.create_run(
        "review", {"dataset_hash": "d" * 64}, config.model_dump(), parent_id=root
    )
    for role, run in [("parent", root), ("reviewer", child), ("judge", root)]:
        with pytest.raises(BoundaryError, match="input"):
            engine._provider_turn(
                run,
                root,
                config,
                ForbiddenTransport(),
                "trusted policy",
                [{"role": "user", "content": "q"}],
                role,
                0,
            )
    published, eid, _ = publication(store)
    store.publish(published, "pub", report(eid))
    monkeypatch.setattr(engine, "provider", lambda _: ForbiddenTransport())
    monkeypatch.setattr(engine, "manifest", lambda _: {"dataset_hash": "d" * 64})
    with pytest.raises(BoundaryError, match="input"):
        live_judge(engine, published, config)
    assert not calls and not store.ledger(root)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM ledger").fetchone()[0] == 0


def test_local_otlp_redaction_and_unavailable_exporter(store, caplog):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    from agent_workbench.telemetry import safe_span, setup

    payloads = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            payloads.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    canary = "SYNTHETIC_AUTH_PROMPT_DATA_ERROR_CANARY_90321"
    try:
        provider = setup(
            store.directory / "otlp-local.jsonl", f"http://127.0.0.1:{server.server_port}/v1/traces"
        )
        with pytest.raises(RuntimeError):
            with safe_span(
                provider.get_tracer("fixture"), "reviewer_child", attributes={"run.id": "synthetic"}
            ):
                raise RuntimeError(canary)
        provider.force_flush()
        provider.shutdown()
        assert payloads
        decoded = ExportTraceServiceRequest.FromString(payloads[0])
        serialized = str(decoded)
        assert (
            "reviewer_child" in serialized
            and "RuntimeError" in serialized
            and canary not in serialized
        )
        assert (
            canary not in (store.directory / "otlp-local.jsonl").read_text()
            and canary not in caplog.text
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    # Explicit failed local export must leave the primary journal/report usable.
    root, eid, _ = publication(store)
    store.publish(root, "pub", report(eid))
    provider = setup(
        store.directory / "unavailable.jsonl", f"http://127.0.0.1:{server.server_port}/v1/traces"
    )
    with safe_span(
        provider.get_tracer("fixture"), "local-export-unavailable", attributes={"run.id": root}
    ):
        pass
    provider.shutdown()
    assert store.report(root)["facts"][0]["value"] == 175


@pytest.mark.parametrize(
    "component",
    [
        "state_schema",
        "engine_behavior",
        "package_version",
        "source",
        "prompt",
        "dataset",
        "recording",
    ],
)
def test_manifest_actual_input_drift(store, tmp_path, monkeypatch, component):
    import shutil

    import agent_workbench.engine as engine_module
    import agent_workbench.util as util_module

    assets = tmp_path / "isolated-assets"
    assets.mkdir()
    for folder in ["datasets", "prompts", "recordings", "sandbox", "src"]:
        shutil.copytree(
            ROOT / folder, assets / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    for name in ["pyproject.toml", "uv.lock"]:
        shutil.copyfile(ROOT / name, assets / name)
    monkeypatch.setattr(engine_module, "ROOT", assets)
    monkeypatch.setattr(util_module, "ROOT", assets)
    engine, root = _real(store)
    if component == "state_schema":
        monkeypatch.setattr(engine_module, "STATE_SCHEMA", 2)
    elif component == "engine_behavior":
        monkeypatch.setattr(engine_module, "ENGINE_VERSION", "changed")
    elif component == "package_version":
        monkeypatch.setattr(engine_module, "__version__", "changed")
    else:
        path = {
            "source": assets / "src/agent_workbench/engine.py",
            "prompt": assets / "prompts/parent-v1.txt",
            "dataset": assets / "datasets/orders.csv",
            "recording": assets / "recordings/total.json",
        }[component]
        path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(IncompatibleResume, match="new run"):
        engine.resume(root)
    assert not store.ledger(root) and not store.containers(root) and store.report(root) is None


@pytest.mark.parametrize("mode", ["anthropic", "openai"])
def test_sdk_engine_retry_ledger_one_for_one(store, mode):
    from tests.unit.test_contracts import response, sdk

    provider, bodies = sdk(mode, [429, response(mode, calls=[("one", "inspect_dataset", {})])])
    config = RunConfig(
        mode=mode,
        model="fixture-model",
        allow_live=True,
        limits=Limits(
            input_token_cap=10000,
            max_tokens=30000,
            output_token_cap=37,
            input_price_per_million=1.0,
            output_price_per_million=2.0,
        ),
    )
    root = store.create_run(
        "Synthetic paid transport retry", {"dataset_hash": "d" * 64}, config.model_dump()
    )
    engine = Engine(store)
    with store.ownership(root):
        turn = engine._provider_turn(
            root, root, config, provider, "policy", [{"role": "user", "content": "q"}], "parent", 0
        )
    rows = store.ledger(root)
    assert len(bodies) == len(rows) == 2 and turn.decision.id == "one"
    assert (
        rows[0]["state"] == "ambiguous"
        and rows[0]["charged_tokens"] == 10037
        and rows[0]["actual_usage"] is None
    )
    assert rows[1]["state"] == "settled" and rows[1]["charged_tokens"] == 10
    assert rows[0]["id"] != rows[1]["id"] and rows[0]["charged_cost"] > rows[1]["charged_cost"]


def test_nested_reviewer_provider_redaction(store, caplog):
    from agent_workbench.providers.scripted import ScriptedProvider

    caplog.set_level(logging.INFO, logger="workbench")
    canary = "SYNTHETIC_NESTED_PROVIDER_AUTH_BODY_456789"
    base = ScriptedProvider(ROOT / "recordings/total.json")

    class NestedFailure:
        def turn(self, system, messages, **kwargs):
            if kwargs["role"] == "reviewer":
                raise RuntimeError(canary)
            return base.turn(system, messages, **kwargs)

    engine = Engine(store, provider=NestedFailure())
    root = engine.create("Nested redaction fixture", RunConfig(scenario="total"))
    with pytest.raises(RuntimeError, match=canary):
        engine.resume(root)
    engine.tracer_provider.force_flush()
    spans = (store.directory / "traces.jsonl").read_text()
    assert "reviewer_child" in spans and "RuntimeError" in spans
    assert (
        canary not in spans
        and canary not in caplog.text
        and canary not in json.dumps(engine.snapshot(root))
    )
    assert store.run(root)["error"] == "RuntimeError" and store.report(root) is None
