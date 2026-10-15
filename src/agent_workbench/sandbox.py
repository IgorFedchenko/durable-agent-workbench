"""Real Docker only: fixed runtime flags, root-scoped cleanup, bounded pipes."""

from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .store import BoundaryError, Store

IMAGE = "durable-agent-sandbox:0.1.0"


class SandboxError(BoundaryError):
    pass


class DockerSandbox:
    def __init__(
        self, store: Store, image: str = IMAGE, wall_seconds: float = 8, output_limit: int = 64000
    ):
        self.store = store
        self.image = image
        self.wall_seconds = wall_seconds
        self.output_limit = output_limit

    def image_identity(self) -> str:
        result = subprocess.run(
            ["docker", "image", "inspect", self.image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            raise SandboxError(
                "Docker sandbox image unavailable; run make sandbox (no host fallback)"
            )
        return result.stdout.strip()

    def cleanup(self, root_id: str) -> None:
        for name in self.store.containers(root_id):
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    name,
                    "--format",
                    '{{index .Config.Labels "workbench.root"}}',
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode == 0:
                if result.stdout.strip() != root_id:
                    raise SandboxError("container ownership label mismatch; refusing cleanup")
                removed = subprocess.run(
                    ["docker", "rm", "-f", name], capture_output=True, timeout=20, check=False
                )
                if removed.returncode:
                    raise SandboxError("failed removing owned container")
            elif "No such" not in result.stderr:
                raise SandboxError("cannot inspect prior owned container")
            self.store.container_removed(name)
        # Root-owned namespace also covers crashes before a container intent exists.
        namespace = self.staging_namespace(root_id)
        if namespace.is_symlink():
            raise SandboxError("staging namespace must not be a symlink")
        if namespace.exists():
            for attempt in namespace.iterdir():
                if attempt.name.startswith("attempt-") and not attempt.is_symlink():
                    shutil.rmtree(attempt)
                else:
                    raise SandboxError("unexpected entry in root staging namespace")
            namespace.rmdir()

    def staging_namespace(self, root_id: str) -> Path:
        uuid.UUID(root_id)
        parent = self.store.directory / "staging"
        if parent.is_symlink():
            raise SandboxError("staging parent must not be a symlink")
        return parent / root_id

    def command(self, name: str, root_id: str, call_id: str, staging: Path) -> list[str]:
        return [
            "docker",
            "create",
            "--name",
            name,
            "--label",
            f"workbench.root={root_id}",
            "--label",
            f"workbench.call={call_id}",
            "--network",
            "none",
            "--log-driver",
            "none",
            "--user",
            "65532:65532",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            "32",
            "--cpus",
            "0.5",
            "--memory",
            "128m",
            "--memory-swap",
            "128m",
            "--ulimit",
            "nofile=64:64",
            "--ulimit",
            "fsize=1048576:1048576",
            "--tmpfs",
            "/scratch:rw,noexec,nosuid,nodev,size=8388608,mode=1777",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=1048576,mode=1777",
            "--mount",
            f"type=bind,src={staging},dst=/input,readonly",
            self.store.run(root_id)["manifest"]["sandbox_image_id"],
        ]

    def execute(
        self, root_id: str, run_id: str, call_id: str, code: str, dataset: Path
    ) -> dict[str, Any]:
        if (
            dataset.is_symlink()
            or dataset.resolve() != dataset.absolute()
            or not dataset.is_file()
            or dataset.stat().st_size > 256000
        ):
            raise SandboxError("dataset must be a bounded regular file, no symlinks")
        if len(code.encode()) > 12000:
            raise SandboxError("source exceeds size bound")
        name = f"daw-{root_id}-{uuid.uuid4().hex[:12]}"
        namespace = self.staging_namespace(root_id)
        if namespace.is_symlink():
            raise SandboxError("staging namespace must not be a symlink")
        namespace.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = namespace / f"attempt-{uuid.uuid4().hex}"
        staging.mkdir(mode=0o755)
        self.store.checkpoint("staging_allocated", {"root_id": root_id, "staging": str(staging)})
        os.chmod(staging, 0o755)
        try:
            (staging / "code.py").write_text(code)
            shutil.copyfile(dataset, staging / "data.csv", follow_symlinks=False)
            for path in staging.iterdir():
                os.chmod(path, 0o444)
            self.store.checkpoint(
                "staging_prepared_before_intent", {"root_id": root_id, "staging": str(staging)}
            )
            # Persist launch intent before any Docker create; crashes leave a cleanup target.
            self.store.container_intent(root_id, run_id, call_id, name, str(staging))
            self.store.checkpoint("container_intent_committed", {"name": name, "root_id": root_id})
            created = subprocess.run(
                self.command(name, root_id, call_id, staging),
                capture_output=True,
                timeout=30,
                check=False,
            )
            if created.returncode:
                raise SandboxError("Docker create failed; no host fallback")
            inspected = subprocess.run(
                ["docker", "inspect", name], capture_output=True, timeout=15, check=True
            )
            inspection = json.loads(inspected.stdout)[0]
            frozen_image = self.store.run(root_id)["manifest"]["sandbox_image_id"]
            if inspection["Image"] != frozen_image:
                raise SandboxError("created container differs from frozen image identity")
            self.store.event(
                run_id,
                "sandbox_created",
                {
                    "container": name,
                    "image_id": inspection["Image"],
                    "host_config": inspection["HostConfig"],
                    "user": inspection["Config"]["User"],
                },
            )
            proc = subprocess.Popen(
                ["docker", "start", "-a", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
            )
            self.store.checkpoint("container_start_dispatched", {"name": name, "root_id": root_id})
            start = time.monotonic()
            output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
            selector = selectors.DefaultSelector()
            assert proc.stdout is not None and proc.stderr is not None
            selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            try:
                while selector.get_map():
                    if time.monotonic() - start > self.wall_seconds:
                        raise SandboxError("sandbox wall timeout")
                    for key, _ in selector.select(0.05):
                        chunk = os.read(key.fd, 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output[key.data].extend(chunk)
                        if sum(map(len, output.values())) > self.output_limit:
                            raise SandboxError("sandbox output bound exceeded")
                proc.wait(timeout=2)
            finally:
                selector.close()
                if proc.poll() is None:
                    subprocess.run(
                        ["docker", "kill", name], capture_output=True, timeout=15, check=False
                    )
                    proc.kill()
                proc.wait(timeout=5)
                proc.stdout.close()
                proc.stderr.close()
            state = json.loads(
                subprocess.run(
                    ["docker", "inspect", name], capture_output=True, timeout=15, check=True
                ).stdout
            )[0]["State"]
            elapsed = time.monotonic() - start
            if state["ExitCode"] != 0:
                return {
                    "ok": False,
                    "error": "analysis_error",
                    "stderr": output["stderr"].decode(errors="replace")[:4000],
                    "exit_code": state["ExitCode"],
                    "oom_killed": state["OOMKilled"],
                    "duration_seconds": elapsed,
                }
            try:
                body = json.loads(
                    output["stdout"],
                    parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"non-finite {x}")),
                )
            except (ValueError, UnicodeDecodeError) as exc:
                raise SandboxError("analysis must output one finite JSON object") from exc
            if not isinstance(body, dict):
                raise SandboxError("analysis output must be an object")
            return {"ok": True, "data": body, "duration_seconds": elapsed}
        finally:
            # If killed with SIGKILL, recovery removes persisted container under root flock.
            try:
                self.cleanup(root_id)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
