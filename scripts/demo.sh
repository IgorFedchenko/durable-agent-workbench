#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'Install uv: https://docs.astral.sh/uv/'; exit 1; }
command -v docker >/dev/null || { echo 'Docker is required; there is no host execution fallback.'; exit 1; }
uv sync --frozen
python3 scripts/build_sandbox.py
echo 'Open http://127.0.0.1:8765 — Ctrl-C stops the server and its workers.'
exec uv run workbench serve
