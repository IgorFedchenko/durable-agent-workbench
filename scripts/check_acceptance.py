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
    required = json.loads(manifest.read_text())
    observed: set[str] = set()
    seen_jobs: set[str] = set()
    count = 0
    for path in directory.rglob("*.xml"):
        job = path.stem
        seen_jobs.add(job)
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            count += 1
            name = case.attrib["name"]
            observed.add(name.split("[")[0])
            if any(case.find(status) is not None for status in ("skipped", "failure", "error")):
                raise SystemExit(f"mandatory case failed/skipped: {name}")
    if not count or seen_jobs != JOBS:
        raise SystemExit(f"missing/unexpected job test reports: {seen_jobs ^ JOBS}")
    missing = set(required["required_test_ids"]) - observed
    if missing:
        raise SystemExit(f"mandatory test IDs never executed: {sorted(missing)}")
    paths = list(directory.rglob("benchmark.json"))
    if len(paths) != 1:
        raise SystemExit("exactly one freshly executed benchmark artifact required")
    benchmark = json.loads(paths[0].read_text())
    case_ids = [case["scenario"] for case in benchmark["cases"]]
    if len(case_ids) != 10 or set(case_ids) != set(required["golden_scenarios"]):
        raise SystemExit("missing/duplicate/unknown mandatory golden cases")
    if not benchmark["aggregate"]["passed"] or not all(c["passed"] for c in benchmark["cases"]):
        raise SystemExit("golden gate failed")
    print(f"Acceptance reports verified: {count} tests; 10/10 golden outcomes; no skips")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path("evals/checks.json"))
    args = parser.parse_args()
    check(args.directory, args.manifest)
