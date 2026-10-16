# Design decisions

| Decision | Why it exists | Tradeoff |
| --- | --- | --- |
| [Explicit durable state machine](adr/001-explicit-state-machine.md) | Make dispatch, recovery and publication inspectable in SQLite. | Local ownership and compatibility checks replace distributed orchestration. |
| [Docker execution boundary](adr/002-docker-boundary.md) | Execute analysis under specific resource and capability limits. | Docker is mandatory; this is not a multi-tenant isolation guarantee. |
| [Authored deterministic evaluation](adr/003-scripted-evaluation.md) | Exercise real infrastructure without paid model calls. | Systems correctness is measurable; model intelligence and live quality are not. |

Read [architecture](architecture.md) for the persistence contracts and [testing](testing.md) for reproduction. These records describe design choices, not evidence that a particular checkout passed its runtime checks.
