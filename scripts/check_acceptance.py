"""Fail closed on missing CI reports, skipped tests, absent checks or incomplete goldens."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

JOBS = {
    "unit-contracts",
    "durability-integration",
    "sandbox-boundaries",
    "browser-e2e",
    "offline-golden",
}


def check(directory: Path, manifest: Path) -> None:
    from agent_workbench.benchmark import validate_benchmark

    required = json.loads(manifest.read_text())
    if required.get("schema_version") != 2 or set(required["jobs"]) != JOBS:
        raise SystemExit("exact per-job mandatory inventory required")
    expected = {job: set(ids) for job, ids in required["jobs"].items()}
    for job, ids in expected.items():
        if not ids or len(ids) != len(required["jobs"][job]):
            raise SystemExit(f"empty/duplicate mandatory inventory: {job}")
    paths = list(directory.rglob("*.xml"))
    if len(paths) != len(JOBS) or {path.stem for path in paths} != JOBS:
        raise SystemExit("exactly one report per mandatory job required")
    count = 0
    for path in paths:
        job = path.stem
        observed: set[str] = set()
        for case in ET.parse(path).getroot().iter("testcase"):
            # pytest's JUnit classname identifies its source module. Keep every parameter suffix.
            node_id = case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"]
            if node_id in observed:
                raise SystemExit(f"duplicate executed case in {job}: {node_id}")
            observed.add(node_id)
            if any(case.find(status) is not None for status in ("skipped", "failure", "error")):
                raise SystemExit(f"mandatory case failed/skipped: {node_id}")
        if not observed or observed != expected[job]:
            raise SystemExit(
                f"{job}: missing={sorted(expected[job] - observed)}; "
                f"unexpected={sorted(observed - expected[job])}"
            )
        count += len(observed)
    benchmarks = list(directory.rglob("benchmark.json"))
    if len(benchmarks) != 1:
        raise SystemExit("exactly one freshly executed benchmark artifact required")
    golden = json.loads((manifest.parent / "golden.json").read_text())
    if set(required["golden_scenarios"]) != set(golden["cases"]):
        raise SystemExit("mandatory scenario inventory differs from frozen golden specification")
    try:
        validate_benchmark(json.loads(benchmarks[0].read_text()), golden)
    except (ValueError, TypeError, KeyError) as exc:
        raise SystemExit(f"invalid executed benchmark: {exc}") from None
    print(
        f"Acceptance reports verified: {count} exact job/parameter cases; 10/10 outcomes; no skips"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path("evals/checks.json"))
    args = parser.parse_args()
    check(args.directory, args.manifest)
