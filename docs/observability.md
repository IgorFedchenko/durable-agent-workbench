# Local observability

The SQLite timeline works without any external service. Each run/provider/tool/reviewer execution emits actual OpenTelemetry spans to `.workbench/traces.jsonl`. Provider spans include known/unknown usage, synthetic provenance and measured dispatch latency. Exported structured logs include allowlisted run/step/attempt/trace IDs, categories and timing; raw prompts, CSV values, keys and SDK error bodies are excluded. The private local journal contains bounded tool inputs/results for debugging and should stay local.

For optional Phoenix, install/run a locally controlled Phoenix instance separately and explicitly pass its loopback HTTP OTLP trace endpoint to `agent_workbench.telemetry.setup(path, otlp_endpoint="http://127.0.0.1:6006/v1/traces")`. The exporter refuses remote hosts/HTTPS and uses a one-second transport timeout. This Python opt-in is separate from `make demo`; no environment variable silently enables export. Phoenix is not needed for mandatory tests or CI. Use `TracerProvider.force_flush()`/`shutdown()` when managing an exporter explicitly.

Telemetry is diagnostic metadata, not proof of correctness. Correlate trace/span IDs with persisted event IDs, provider reservations, call IDs and image/data/config hashes. Avoid sharing a local SQLite database or traces containing private questions; all included fixtures are synthetic.
