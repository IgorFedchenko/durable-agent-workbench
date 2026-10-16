# Providers and opt-in spending

The normalized provider protocol permits exactly one tool call or one strict structured final answer per turn. Parallel/multiple calls reject explicitly. Tool call IDs survive the next request as OpenAI `tool_call_id` or Anthropic `tool_use_id`. Unknown tools/arguments, invalid numeric fields, refusal and truncation fail clearly. There is no silent truncation or unbounded schema repair.

The adapters use the official OpenAI Chat Completions and Anthropic Messages SDKs, pinned exactly by `uv.lock`. SDK retries are zero; engine retries each need a new durable reservation. Local provider-shaped transport fixtures are provided to check request/response contracts; executing them is part of [local verification](testing.md). **Both adapters and the live judge remain live-unverified until an authorized live run produces evidence.** No live quality or billing benchmark is claimed.

The default demo/CLI/UI/eval use no paid transport. Keys come only from environment and never appear in UI or config files. Web requests cannot enable live mode. A manual smoke requires all of the following, using your chosen provider's actual exact model and your configured worst-case price assumptions:

```sh
# Set OPENAI_API_KEY (or ANTHROPIC_API_KEY) in your shell without writing it to a file.
uv run workbench live-smoke --allow-live --provider openai \
  --model YOUR_EXACT_MODEL --input-price YOUR_USD_PER_MILLION \
  --output-price YOUR_USD_PER_MILLION --max-output 1500 --max-cost 0.25
```

Live judge uses a separate frozen root ledger over a published report:

```sh
uv run workbench judge RUN_ID --allow-live --provider anthropic \
  --model YOUR_EXACT_MODEL --input-price YOUR_USD_PER_MILLION \
  --output-price YOUR_USD_PER_MILLION --max-output 1500 --max-cost 0.25
```

These examples are intentionally incomplete and make no network call until complete opt-in config and the matching environment key are supplied. Every transport attempt reserves the configured full input/output allowance and price first. Missing usage remains unknown and conservatively charged. These are application-configured admission bounds, not guarantees of actual provider billing. An unknown outcome after a process crash may be billed; a retry may be another paid request.
