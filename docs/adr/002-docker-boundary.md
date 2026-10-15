# ADR 002: Real constrained Docker execution

Status: accepted.

Untrusted Python runs in a fixed local Docker image with no network, non-root user, read-only root/input, dropped capabilities and cgroup/wall/output/disk bounds. The host receives bounded JSON and renders charts itself. Models cannot choose mount paths or runtime flags. There is no host fallback when Docker is absent.

Docker availability/build time is a deliberate prerequisite. Docker shares a kernel and trusts the local daemon/host; this is a local demonstration of named tested controls, not a hostile multi-tenant security product. Real boundary probes and inspect evidence are required instead of claims based only on configuration text.
