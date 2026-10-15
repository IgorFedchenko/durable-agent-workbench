# ADR 003: Authored deterministic systems evaluation

Status: accepted.

Default provider and judge responses are author-written fixtures with explicit synthetic token counts. This keeps the full infrastructure demonstration reproducible and free of paid API calls. Frozen independently calculated goldens, safety negative controls and real process/Docker/browser checks evaluate the implementation rather than pretending authored responses measure model intelligence.

Official SDK adapters and manual live judge/smoke paths share the trusted boundaries but remain live-unverified. Live model quality, actual prices and provider latency need separately authorized evidence. Keep scenario outcome rate separate from published-report count and all synthetic usage separate from actual API spend.
