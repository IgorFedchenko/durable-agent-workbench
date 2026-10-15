"""Prove the installed secret scanner catches a generated, synthetic token shape."""

from __future__ import annotations

import secrets
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    # Construct at runtime so no credential-shaped literal enters repository history.
    token = "gh" + "p_" + secrets.token_hex(18)
    with tempfile.TemporaryDirectory(prefix="workbench-secret-control-") as directory:
        fixture = Path(directory) / "synthetic.env"
        fixture.write_text("GITHUB_TOKEN=" + token + "\n")
        result = subprocess.run(
            ["gitleaks", "dir", "--redact", "--no-banner", directory],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 1 or "leaks found" not in result.stderr.lower():
            raise SystemExit("Secret detector positive control failed")
        if token in result.stdout or token in result.stderr:
            raise SystemExit("Secret scanner exposed an unredacted synthetic token")
    print("Secret detector positive control passed; generated fixture removed")


if __name__ == "__main__":
    main()
