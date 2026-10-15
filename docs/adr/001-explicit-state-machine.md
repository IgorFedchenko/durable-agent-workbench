# ADR 001: Explicit local state machine and durable ownership

Status: accepted.

Use SQLite WAL plus a lifetime root flock on a local filesystem. The small workflow has auditable decision/call/reviewer/publication transitions; a general orchestration framework would hide the particular recovery boundaries this example teaches. Immutable decisions and argument hashes permit completed result replay. Root-scoped OS ownership prevents concurrent dispatch without unreliable time leases.

The tradeoff is single-host execution and operator intervention for a stalled owner. Pure work may replay after an ambiguous crash. Only the SQLite publication/report-result transaction has a stable exactly-once key; arbitrary external effects do not inherit that property. Incompatible engine/data/prompt/image/config versions require a new run instead of automatic migration.
