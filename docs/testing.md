# Local verification

The default suite executes authored provider recordings against real SQLite, subprocess recovery, Docker and a local browser. It makes no live model calls. Official SDK adapters have local transport-contract fixtures; live provider behavior, billing and model quality remain unverified. No credentials or hosted services are needed.

## Prerequisites and commands

Use Python 3.12+, uv, Docker Engine/Desktop with buildx and a local Linux/macOS filesystem. The sandbox builder verifies its project-owned BuildKit service has at most two CPUs and four GiB. Dependency/image/browser installation needs internet; default runtime does not contact a model.

```sh
uv sync --frozen --group dev
uv run playwright install chromium
make sandbox
make quality
make secrets       # install local gitleaks first
make test
make eval
```

`make demo` is the one-command local launch, including dependency synchronization and sandbox build. Open `http://127.0.0.1:8765`; Ctrl-C stops its server/workers. `make test` is the one-command full test check after prerequisites. Missing Docker/browser infrastructure fails rather than silently skipping mandatory checks. If your host requires job scheduling, use its command prefix through `make JOB="your-local-job-runner --" test`, with one scheduling layer per command. The application does not depend on a particular scheduler.

## What is checked

| Suite | Boundary exercised |
| --- | --- |
| `tests/unit` | Provider-shaped transport contracts, validation, budgets and state invariants. |
| `tests/integration` | Real process termination/recovery, root ownership and persisted dispatch/publication. |
| `tests/sandbox` | Real Docker resource, filesystem and network restrictions. |
| `tests/e2e` | Real browser against the local HTTP debugger and execution path. |
| `tests/evals` | Frozen synthetic scenarios and independent numeric goldens. |

`make quality` checks Ruff lint/format and strict mypy. `make secrets` verifies the detector with an ephemeral synthetic positive control, then scans Git history and the working tree with redacted Gitleaks output. Test outcomes are recorded in `.workbench/test-results/all.xml`; skips and unexpected passes cannot substitute for the required executed inventory.

`make eval` writes measured per-case evidence to `evals/benchmark.json`. The frozen gate requires ten scenario outcomes, at most 24 execution steps, 20,000 synthetic tokens and 180 seconds per case, with zero actual API spend. These limits are acceptance requirements, not benchmark results. Scenario success and published-report count are separate. See [evaluation scope](evaluation.md).

The retained hosted workflow recipe shows the five-suite report layout and `scripts/check_acceptance.py` verifies exact job/parameter identities plus a fresh benchmark. A single full-suite JUnit file is useful local evidence but is not that five-report input layout. GitHub Actions is disabled while this repository is private, and every recipe job has a public-repository guard. No hosted CI execution or green badge is claimed. Optional CodeQL and live-smoke recipes also require a public repository and explicit manual opt-in; no live credentials are connected. Dependency-update automation is retained as an [inactive Dependabot template](examples/dependabot.yml), outside GitHub’s active configuration path. Activate it only after general review and an explicit public-repository decision. Existing dependency PRs and branches are preserved.

## Visual evidence and limits

After prerequisites, `make capture` drives the real browser/server/engine/Docker path and writes `docs/demo.gif`. A GIF is evidence only after capture succeeds; this documentation does not claim an existing recording. No screenshots or benchmark numbers are fabricated.

The suite evaluates systems behavior with authored responses. It does not measure live model reasoning, production provider availability, cloud security, distributed execution or general truth of arbitrary generated analysis. See [threat model](threat-model.md), [providers](providers.md) and [architecture](architecture.md).
