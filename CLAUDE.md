# Durable Agent Workbench contributor instructions

This file is the canonical instruction source for agents working in this repository.

## Structure

- `src/agent_workbench/`: engine, SQLite store, Docker boundary, provider adapters, local HTTP debugger and CLI.
- `tests/`: unit contracts, process durability, sandbox boundaries, browser checks and offline evaluation.
- `datasets/`, `prompts/`, `recordings/`, `evals/`: synthetic inputs, authored responses and frozen evaluation contracts.
- `docs/`: public architecture, decisions, threat model and reproduction guidance.
- `scripts/`: sandbox build, local demo/capture and acceptance/secret checks.

## Build and check

Use Python 3.12+, uv and Docker with buildx on a local Linux/macOS filesystem. Install pinned dependencies with `uv sync --frozen --group dev`; install Chromium with `uv run playwright install chromium`. `make demo` builds the sandbox and starts the loopback debugger. `make sandbox` builds separately. `make quality`, `make secrets`, `make test` and `make eval` are the local checks; see `docs/testing.md` for prerequisites and artifacts. Use the generic `JOB` Make variable when a host requires a scheduler; host-specific policy belongs outside this repository.

## Rules

Preserve the explicit state machine and persisted-before-dispatch ordering. Keep parent/reviewer budgets shared, evidence immutable, numeric citations validated and publication atomic. Do not introduce host execution fallback, broad Docker cleanup, hidden retries or optional skips for mandatory infrastructure tests. Change frozen goldens only with a documented behavioral reason and independent calculation.

Default execution must stay local and scripted. Do not make paid/live provider calls, add credentials, connect hosted services or enable GitHub Actions as part of routine checks. Provider adapters are opt-in capabilities; transport fixtures do not establish live correctness. Keep synthetic token accounting distinct from real usage and measured results distinct from requirements.

Keep this repository private until owner approval after independent final review. Do not publish, push, rewrite history, alter attribution/dates or enable private Actions. New authorized commits carry `[skip ci]`. Keep internal plans, reviews, coordination logs, credentials and employer materials outside the product repository. Update this file for durable contributor rules, never session status; `AGENTS.md` is only a pointer.
