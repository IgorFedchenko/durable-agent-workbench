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

| Suite | Executed cases | Boundary exercised |
| --- | --- | --- |
| `tests/unit` | 28 | Provider-shaped transport contracts, validation, budgets and state invariants. |
| `tests/integration` | 45 | Real process termination/recovery, root ownership and persisted dispatch/publication. |
| `tests/sandbox` | 12 | Real Docker resource, filesystem and network restrictions. |
| `tests/e2e` | 9 | Real browser against the local HTTP debugger and execution path. |
| `tests/evals` | 11 | Frozen synthetic scenarios and independent numeric goldens. |

`make quality` checks Ruff lint/format and strict mypy. `make secrets` verifies the detector with an ephemeral synthetic positive control, then scans Git history and the working tree with redacted Gitleaks output. Test outcomes are recorded in `.workbench/test-results/all.xml`; skips and unexpected passes cannot substitute for the required executed inventory.

`make eval` writes measured per-case evidence to `evals/benchmark.json`. The frozen gate requires ten scenario outcomes, at most 24 execution steps, 20,000 synthetic tokens and 180 seconds per case, with zero actual API spend. These limits are acceptance requirements, not benchmark results. Scenario success and published-report count are separate. See [evaluation scope](evaluation.md).

The retained hosted workflow recipe shows the five-suite report layout and `scripts/check_acceptance.py` verifies exact job/parameter identities plus a fresh benchmark. A single full-suite JUnit file is useful local evidence but is not that five-report input layout. GitHub Actions is disabled while this repository is private, and every recipe job has a public-repository guard. No hosted CI execution or green badge is claimed. Optional CodeQL and live-smoke recipes also require a public repository and explicit manual opt-in; no live credentials are connected. Dependency-update automation is retained as an [inactive Dependabot template](examples/dependabot.yml), outside GitHub’s active configuration path. Activate it only after general review and an explicit public-repository decision. Existing dependency PRs and branches are preserved.

## Executed local baseline and budget-test scope

The full suite passed **105/105 cases in 137.63 seconds** at source `4b126a399edb4426d1392f2d18c641a1de8e479a`, with zero failures, errors or skips. All five groups above executed, including real Docker and browser checks. The sandbox image was `sha256:b299a6a235d150e76d18e58b4d80a1a93cad4a2389940c0a21bbb955b72e163a`, running as UID 65532. Reproduce after prerequisites with `make test`. This result does not establish a pass for changed product or test source.

The separate [scripted benchmark](evaluation.md#measured-local-baseline) ran at `f075ab8` with the same runtime source fingerprint and produced ten expected outcomes and nine reports, with $0 actual API spend. Summed scenario wall time was 7.439 seconds; the slowest case was 1.442 seconds. The older `f030712` raw baseline remains unchanged. Reproduce with `make eval`; measured times are host-specific systems timings, not model/provider latency.

The parent/child budget race test also exercises actual Engine admission into a local `FakePaid` provider and counts invocations across cost, token and step limits. Its Decimal $0.030 charge is synthetic accounting; it makes no external HTTP/provider call and spends $0 in actual API fees. This strengthens the SQLite reservation-race check with an Engine dispatch observation. A separate crash check covers the production `Store.mark_ambiguous` + `Engine._provider_turn` recovery boundary, rather than the whole `Engine.resume` journey. A spawned worker enters a blocking local `FakePaid`, fsyncs its transport receipt, and is killed with SIGKILL (exit −9) before any response. The replacement keeps the full unknown charge (10,010 reserved tokens and synthetic $0.030), refuses a further dispatch specifically on the budget ceiling, and leaves the ledger unchanged with exactly one recorded invocation. This is local transport instrumentation, not live HTTP or actual spending. Targeted checks and the full suite at `4b126a3` passed. Live SDK adapters and live judging remain live-unverified.

## Focused publication and context regressions

A focused reviewer-binding check passed with an accepted verdict followed by an explicitly failed child: publication refused without adding a report or completed publication call, and root/child status stayed unchanged. Accepted pending/running children remain positive controls for the verdict-before-completion crash window. This is a direct Store boundary check, not evidence of a normal Engine exploit.

Two focused process/context checks also passed. Separate publisher processes exercise root-lock contention: the losing owner enters publication zero times, then replays the single committed report after ownership becomes available; changed arguments reject. The context check captures actual `Engine._execute` provider inputs before and after real compaction, positively locating hostile CSV, evidence and replayed synthetic error content in untrusted messages while checking trusted policy/tool boundaries. Its failed tool result is seeded and replayed; it does not generate a Docker error or test a live model's resistance.

These focused checks are also included in the 105-case pass at `4b126a3`. The primary benchmark measurements are tied to `f075ab8`; their source fingerprint matches the tested runtime source.

## Visual evidence and limits

After prerequisites, `make capture` drives the real browser/server/engine/Docker path. A successful full journey writes `docs/demo.gif` and `docs/portfolio-cover.png`; failure exits nonzero without publishing these assets. Fresh state and diagnostics remain under a unique ignored `.workbench/capture-*/` directory, including server logs/PID status, browser trace, a failure screenshot when available and read-only run snapshots.

To select fresh retained directories and a free loopback port explicitly:

```sh
uv run python scripts/capture_demo.py --data-dir .workbench/my-capture-data \
  --evidence-dir .workbench/my-capture-evidence --port 8766
```

Both directories must not already exist. The script starts and stops only its own server and refuses an occupied port. The included [GIF](demo.gif) and [1000 × 750 cover](portfolio-cover.png) were captured from the actual browser/server/engine/Docker journey at source `e5a004f36edaf0f67a5bddf5e7863c3673cdcefa`, whose runtime source fingerprint matches the full-suite source `4b126a3`. The GIF shows failed analysis, explicit resume, reviewer linkage and verified reports; the cover shows the completed regional chart and expanded reviewer timeline. Chromium rendered the cover directly; neither file was resized, reencoded after capture or fabricated.

Media SHA-256: `demo.gif` — `ac3ba09fd21491389de27851918dbe939422a50f58293a18b09071e4545bf0bf`; `portfolio-cover.png` — `7cdcf4a54f78608e1f19d1b972f4e71c207af61d8640fd9099fbde9dea763773`.

The suite evaluates systems behavior with authored responses. It does not measure live model reasoning, production provider availability, cloud security, distributed execution or general truth of arbitrary generated analysis. See [threat model](threat-model.md), [providers](providers.md) and [architecture](architecture.md).
