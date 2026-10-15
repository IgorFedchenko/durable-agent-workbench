"""Independent literal oracle and reusable process fixtures; no expected values from recordings."""

from __future__ import annotations

import csv
import multiprocessing as mp
import os
import signal
import time
from pathlib import Path

from agent_workbench.models import Limits, Report, RunConfig, Verdict
from agent_workbench.store import Store
from agent_workbench.util import ROOT

ROWS = [
    (1, "2026-01", "north", "widget", 2, 10),
    (2, "2026-01", "south", "widget", 1, 10),
    (3, "2026-02", "north", "widget", 3, 10),
    (4, "2026-02", "south", "gadget", 2, 25),
    (5, "2026-03", "north", "gadget", 1, 25),
    (6, "2026-03", "south", "widget", 4, 10),
]
SCENARIOS = [
    "total",
    "regions",
    "months",
    "filter",
    "empty-filter",
    "repair",
    "injection",
    "compaction",
    "interruption",
    "reviewer-rejection",
]


def oracle(scenario="total"):
    rows = [
        r for r in ROWS if (scenario != "filter" or r[3] == "gadget") and scenario != "empty-filter"
    ]
    chart = {}
    if scenario in ("regions", "months"):
        index = 2 if scenario == "regions" else 1
        for row in rows:
            chart[row[index]] = chart.get(row[index], 0) + row[4] * row[5]
    return sum(r[4] * r[5] for r in rows), len(rows), chart


def assert_dataset():
    with (ROOT / "datasets/orders.csv").open() as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == [
            "id",
            "month",
            "region",
            "product",
            "units",
            "unit_price",
            "note",
        ]
        actual = [
            (
                int(r["id"]),
                r["month"],
                r["region"],
                r["product"],
                int(r["units"]),
                int(r["unit_price"]),
            )
            for r in reader
        ]
    assert actual == ROWS
    assert oracle() == (175, 6, {})
    assert oracle("regions")[2] == {"north": 75, "south": 100}
    assert oracle("months")[2] == {"2026-01": 30, "2026-02": 80, "2026-03": 65}
    assert oracle("filter")[:2] == (75, 2)


def raw_run(store, *, live=False, limits=None, parent=None):
    config = RunConfig(
        mode="openai" if live else "scripted",
        model="fixture-model" if live else "author-written-v1",
        allow_live=live,
        limits=limits or Limits(),
    )
    return store.create_run(
        "Independent fixture", {"dataset_hash": "d" * 64}, config.model_dump(), parent_id=parent
    )


def report(eid, value=175.0, field="total", chart=None):
    return Report.model_validate(
        {
            "title": "Independent report",
            "explanation": "Literal synthetic arithmetic.",
            "facts": [
                {
                    "label": "Revenue",
                    "value": float(value),
                    "citation": {"evidence_id": eid, "field": field},
                }
            ],
            "chart": chart or [],
        }
    )


def publication(store, body=None, accepted=True):
    root = raw_run(store)
    store.begin_call(root, "analysis", "analyze", {"code": "fixture"})
    store.finish_call(root, "analysis", {"ok": True})
    eid = store.add_evidence(
        root,
        "analysis",
        body or {"total": 175, "orders": 6, "regions": {"north": 75, "south": 100}},
    )
    child = raw_run(store, parent=root)
    store.status(child, "completed")
    store.save_verdict(
        root,
        child,
        Verdict(accepted=accepted, evidence_ids=[eid], reason="Independent fixture verdict"),
    )
    return root, eid, child


def paid_limits(**changes):
    return Limits(
        input_token_cap=10,
        output_token_cap=10,
        input_price_per_million=1000.0,
        output_price_per_million=2000.0,
        max_cost_usd=0.05,
        **changes,
    )


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError("bounded fixture barrier not reached")


def _engine_worker(directory, root, barrier, connection, match):
    from agent_workbench.engine import Engine

    def checkpoint(name, payload):
        if name == barrier and all(payload.get(k) == v for k, v in match.items()):
            connection.send({"name": name, "payload": payload, "pid": os.getpid()})
            connection.recv()

    try:
        Engine(Store(Path(directory), checkpoint=checkpoint)).resume(root)
    except Exception as exc:
        connection.send({"error": type(exc).__name__, "message": str(exc)})
    finally:
        connection.close()


class Worker:
    def __init__(self, directory, root, barrier, **match):
        ctx = mp.get_context("spawn")
        self.connection, child = ctx.Pipe()
        self.process = ctx.Process(
            target=_engine_worker, args=(str(directory), root, barrier, child, match)
        )
        self.process.start()
        child.close()

    def reached(self, timeout=45):
        assert self.connection.poll(timeout), "worker did not reach committed checkpoint"
        message = self.connection.recv()
        assert "error" not in message, message
        return message

    def kill(self):
        self.process.kill()
        self.process.join(5)
        assert self.process.exitcode == -signal.SIGKILL

    def close(self):
        if self.process.is_alive():
            self.kill()
        self.connection.close()
