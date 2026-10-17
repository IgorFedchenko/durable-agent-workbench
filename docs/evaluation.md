# Evaluation contract

Default responses are **AUTHOR-WRITTEN SCRIPTED RECORDINGS**, not captured paid-model output. The engine, Docker, SQLite, HTTP, chart and process recovery are real. These scenarios measure deterministic systems outcomes. They do not measure live-model intelligence, accuracy, judge quality or provider latency.

`evals/golden.json` contains expectations frozen before implementation: total 175 from six orders; north/south 75/100; January/February/March 30/80/65; gadget 75 from two orders; absent product 0/0 and no-data label. The mandatory scenarios are total, regions, months, filter, empty-filter, repair, injection, compaction, interruption and reviewer-rejection. Rejection passes the scenario gate only when publication fails with zero reports. Report count is separate from scenario outcome count.

Run `make eval` against actual Docker. The runner executes all ten cases, including a separate worker SIGKILL after committed analysis and a replacement process over the same database. It verifies completed analysis does not relaunch. The emitted JSON includes each immutable manifest, scenario observations, failures, synthetic token/root-step totals, $0 actual API spend, measured wall latency and process recovery evidence. Code/source, image, prompt, recording, config and dataset hashes identify what ran. A run is not a baseline merely because it produced a file.

The acceptance gate requires ten of ten expected outcomes, at most 24 aggregate steps and 20,000 labeled synthetic tokens per case, and a 180-second case ceiling. Steps count provider attempts and new tool attempts across parent/reviewer/retries. Latency is measured on the executing host and is not a cross-host speed comparison. Missing, duplicate, skipped or timed-out mandatory cases fail. Independent tests calculate their own arithmetic from literal rows; they must not import expected answers from recordings.

Safety/recovery checks and negative controls are also mandatory: consistently wrong 174 analysis despite agreeing scripted actors; altered/uncited/foreign numeric citations; rejected/missing reviewer; wrong call identity; forbidden/multiple calls; malformed/refused/truncated SDK output; absent live prices/usage; process ownership/crashes; effective Docker boundaries; web request protections and escaped rendering. `evals/checks.json` freezes exact per-job module/function IDs, including each provider parameter variant. The acceptance checker rejects missing, wrong-job, duplicate, unexpected, skipped/error or zero-collection cases in any required suite. A strict benchmark schema checks complete provenance, metrics, numeric observations, case/aggregate agreement and frozen ceilings rather than trusting passed booleans. All required suites enforce strict XPASS handling through the installed pytest policy. Per-test `strict=False` cannot weaken it; any residual XPASS becomes a failed JUnit result. Expected xfail and skipped results also fail acceptance. Tests are developed independently from the golden specification.

Four semantic verification mutations target ownership, publication evidence binding, durable reservation and runtime networking. Each must make its corresponding invariant assertion fail, then the restored check/full suite must pass. A syntax error or test collection failure is not a valid mutation kill.

## Measured local baseline

One local scripted **systems benchmark** at source `f075ab8c16406283ed25d724a6df86960713c5bd` achieved **10/10 expected outcomes and 9 published reports**; reviewer rejection correctly published none. Real Docker execution and process recovery ran with author-written provider responses. This measures systems behavior, not model accuracy or provider latency.

| Measurement | Observed in this run | Frozen requirement per case |
| --- | --- | --- |
| Scenario wall time | 7.439 s summed; 1.442 s slowest case | ≤180 s |
| Parent + child execution steps | 13 maximum per case | ≤24 |
| Authored synthetic tokens | 1,620 maximum per case | ≤20,000 |
| Actual API spend | $0 | $0 |

Timings are host-specific scenario measurements, excluding environment setup, queueing and the separate full test suite. The unchanged [raw benchmark](benchmarks/scripted-f075ab8.json) records full source/image/input hashes, observations and recovery proofs. Its SHA-256 is `7c7db97da5930b1125cf3365d3cae79f6f1b198f8a63c78dffeb176161577cc0`. The interruption case records a SIGKILLed worker (−9), successful replacement (0), and one analysis launch both before and after recovery.

The earlier [f030712 baseline](benchmarks/scripted-f030712.json) remains unchanged for comparison: SHA-256 `7bab8a38e5eae8857a43ad5b0561c768ec7b5227d0e5d21e5361616b2e1e0cfd`. Each JSON identifies its own measured source; neither artifact is relabeled as a later documentation commit.

## Judge scope

`workbench judge RUN_ID` validates a strict correctness/evidence/clarity/safety rubric using an authored offline fixture. Provenance includes judged report/dataset hashes, rubric/prompt hash, decision hash, model identity and bounded scores. It validates rubric/schema/provenance plumbing only. Judge scores never override deterministic arithmetic, publication or safety gates; disagreements remain separate results.

Optional live judging and provider smoke require explicit flags, environment keys, exact model and configured prices/output/spend bounds. Until separately executed, both SDK adapters and live judge are **contract-tested / live-unverified**. Provider-shaped tests run pinned official SDK serialization and response parsing through local synthetic HTTP transports. They are not paid provider calls.

## Local checks and retained workflow recipes

GitHub Actions is disabled while this repository is private. Every retained workflow job requires a public repository; optional CodeQL/live smoke additionally require manual opt-in. No private hosted CI or paid security plan is enabled. The recipes preserve named quality, unit-contracts, durability-integration, sandbox-boundaries, browser-e2e, offline-golden, secret-scan and acceptance checks for separately approved future use. Docker/browser checks fail on missing prerequisites. Gitleaks scans local history and files independently of GitHub licensing. See [local verification](testing.md).
