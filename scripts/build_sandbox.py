"""Verify the project-owned BuildKit service cgroup before building."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "durable-agent-builder"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


if subprocess.run(["docker", "buildx", "inspect", NAME], capture_output=True).returncode:
    run(
        "docker",
        "buildx",
        "create",
        "--name",
        NAME,
        "--driver",
        "docker-container",
        "--driver-opt",
        "memory=4g,cpu-period=100000,cpu-quota=200000",
    )
run("docker", "buildx", "inspect", NAME, "--bootstrap")
config = json.loads(run("docker", "inspect", f"buildx_buildkit_{NAME}0").stdout)[0]["HostConfig"]
memory, period, quota = config["Memory"], config["CpuPeriod"], config["CpuQuota"]
if not (0 < memory <= 4 * 1024**3 and period > 0 and 0 < quota / period <= 2):
    raise SystemExit("BuildKit service lacks verified <=2 CPU/4 GiB limits; refusing build")
print(
    json.dumps({"builder": NAME, "memory_bytes": memory, "cpu_period": period, "cpu_quota": quota}),
    flush=True,
)
subprocess.run(
    [
        "docker",
        "buildx",
        "build",
        "--builder",
        NAME,
        "--load",
        "-t",
        "durable-agent-sandbox:0.1.0",
        str(ROOT / "sandbox"),
    ],
    check=True,
)
