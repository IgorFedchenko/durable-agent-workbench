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

Optional contributor hooks use an isolated launcher; pre-commit is not a project dev dependency:

```sh
uvx --python 3.12 pre-commit install
```

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

## Local baseline and budget-test scope

The full 105-case suite passed at source `f030712` in 129.22 seconds. The separate [scripted benchmark](evaluation.md#measured-local-baseline) produced ten expected outcomes and nine reports. These are recorded baselines, not a claim that later revisions or independent final verification have passed.

The parent/child budget race test also exercises actual Engine admission into a local `FakePaid` provider and counts invocations across cost, token and step limits. Its Decimal $0.030 charge is synthetic accounting; it makes no external HTTP/provider call and spends $0 in actual API fees. This strengthens the SQLite reservation-race check with an Engine dispatch observation. A separate crash check covers the production `Store.mark_ambiguous` + `Engine._provider_turn` recovery boundary, rather than the whole `Engine.resume` journey. A spawned worker enters a blocking local `FakePaid`, fsyncs its transport receipt, and is killed with SIGKILL (exit −9) before any response. The replacement keeps the full unknown charge (10,010 reserved tokens and synthetic $0.030), refuses a further dispatch specifically on the budget ceiling, and leaves the ledger unchanged with exactly one recorded invocation. This is local transport instrumentation, not live HTTP or actual spending. Targeted checks passed; final restored-suite and mutation verification remain pending. Live SDK adapters and live judging remain live-unverified.

## Focused publication and context regressions

A focused reviewer-binding check passed with an accepted verdict followed by an explicitly failed child: publication refused without adding a report or completed publication call, and root/child status stayed unchanged. Accepted pending/running children remain positive controls for the verdict-before-completion crash window. This is a direct Store boundary check, not evidence of a normal Engine exploit.

Two focused process/context checks also passed. Separate publisher processes exercise root-lock contention: the losing owner enters publication zero times, then replays the single committed report after ownership becomes available; changed arguments reject. The context check captures actual `Engine._execute` provider inputs before and after real compaction, positively locating hostile CSV, evidence and replayed synthetic error content in untrusted messages while checking trusted policy/tool boundaries. Its failed tool result is seeded and replayed; it does not generate a Docker error or test a live model's resistance.

These targeted results do not establish a final full-suite or benchmark pass for the integrated source. The measured baseline above remains tied to `f030712`.

## Visual evidence and limits

After prerequisites, `make capture` drives the real browser/server/engine/Docker path. A successful full journey writes `docs/demo.gif` and `docs/portfolio-cover.png`; failure exits nonzero without publishing these assets. Fresh state and diagnostics remain under a unique ignored `.workbench/capture-*/` directory, including server logs/PID status, browser trace, a failure screenshot when available and read-only run snapshots. This retained project-local state is a diagnostic choice; it does not establish the cause of an earlier capture failure.

To select fresh retained directories and a free loopback port explicitly:

```sh
uv run python scripts/capture_demo.py --data-dir .workbench/my-capture-data \
  --evidence-dir .workbench/my-capture-evidence --port 8766
```

Both directories must not already exist. The script starts and stops only its own server and refuses an occupied port. A GIF is evidence only after capture succeeds; this documentation does not claim an existing recording. No screenshots or benchmark numbers are fabricated.

The suite evaluates systems behavior with authored responses. It does not measure live model reasoning, production provider availability, cloud security, distributed execution or general truth of arbitrary generated analysis. See [threat model](threat-model.md), [providers](providers.md) and [architecture](architecture.md).
