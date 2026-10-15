"""Explicitly synthetic checker inputs. Never execution evidence or measured benchmarks."""

import json
import uuid
import xml.etree.ElementTree as ET

from agent_workbench.util import object_hash
from tests.helpers import SCENARIOS, oracle


def synthetic_benchmark():
    cases = []
    for scenario in SCENARIOS:
        total, orders, chart = oracle(scenario)
        root = str(uuid.uuid4())
        eid = "ev-schema-fixture"
        expected = {"total": total, "orders": orders}
        flags = {
            "repair": {"analysis_failure": True},
            "compaction": {"compaction": True, "late_retrieval": True},
            "interruption": {"process_kill_resume": True},
            "empty-filter": {"no_data": True},
        }
        expected.update(flags.get(scenario, {}))
        if chart:
            expected["chart"] = chart
        if scenario == "reviewer-rejection":
            expected = {"rejected": True, "reports": 0}

        def point(label, value, field, eid=eid):
            return {
                "label": label,
                "value": float(value),
                "citation": {"evidence_id": eid, "field": field},
            }

        report = {
            "title": "SYNTHETIC SCHEMA FIXTURE",
            "explanation": "Not actual execution or measured benchmark.",
            "facts": [point("Revenue", total, "total"), point("Orders", orders, "orders")],
            "chart": [
                point(label, value, ("regions." if scenario == "regions" else "months.") + label)
                for label, value in chart.items()
            ],
            "no_data": scenario == "empty-filter",
        }
        manifest = {
            "state_schema": 1,
            "engine_behavior": "1",
            "package_version": "schema-fixture",
            "source_fingerprint": "a" * 64,
            "sandbox_image_id": "sha256:" + "b" * 64,
            "prompts": {p: "c" * 64 for p in ["parent-v1.txt", "reviewer-v1.txt", "judge-v1.txt"]},
            "dataset_hash": "d" * 64,
            "recording_hash": "e" * 64,
            "config_hash": "f" * 64,
            "provider": "scripted",
            "model": "author-written-v1",
            "pricing": {"input": None, "output": None},
        }
        cases.append(
            {
                "scenario": scenario,
                "passed": True,
                "failures": [],
                "run_id": root,
                "expected": expected,
                "observed_report": None if scenario == "reviewer-rejection" else report,
                "verdict": {
                    "accepted": scenario != "reviewer-rejection",
                    "evidence_ids": [eid],
                    "reason": "Authored schema fixture",
                },
                "status": "failed" if scenario == "reviewer-rejection" else "completed",
                "metrics": {
                    "steps": 1,
                    "charged_tokens": 1,
                    "charged_cost_usd": 0.0,
                    "usage_unknown": False,
                    "actual_api_spend_usd": 0,
                },
                "wall_seconds": 0.1,
                "manifest": manifest,
                "manifest_hash": object_hash(manifest),
                "recovery": {
                    "worker_pid": 1,
                    "exitcode": -9,
                    "replacement_exitcode": 0,
                    "barrier": {"committed": True, "run_id": root},
                    "analysis_launches_before": 1,
                    "analysis_launches_after": 1,
                }
                if scenario == "interruption"
                else None,
                "provider_provenance": (
                    "AUTHOR-WRITTEN SCRIPTED RECORDINGS; synthetic tokens; $0 actual API spend"
                ),
            }
        )
    return {
        "schema_version": 1,
        "code": {
            "git_sha": "1" * 40,
            "package_version": "schema-fixture",
            "source_fingerprint": "a" * 64,
        },
        "evaluation": "deterministic systems outcomes; not model accuracy",
        "usage": "labeled synthetic tokens; author-written scripted recordings",
        "cases": cases,
        "aggregate": {
            "passed": True,
            "scenario_outcomes_passed": 10,
            "mandatory_cases": 10,
            "published_reports": 9,
            "actual_api_spend_usd": 0,
            "wall_seconds": sum(c["wall_seconds"] for c in cases),
        },
    }


def synthetic_junit(directory, inventory):
    for job, ids in inventory["jobs"].items():
        suite = ET.Element("testsuite", name="SYNTHETIC-CHECKER-FIXTURE")
        for node in ids:
            module, name = node.split("::")
            ET.SubElement(suite, "testcase", classname=module[:-3].replace("/", "."), name=name)
        ET.ElementTree(suite).write(directory / f"{job}.xml")
    (directory / "benchmark.json").write_text(json.dumps(synthetic_benchmark()))
