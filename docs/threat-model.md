# Threat model and tested-control scope

The model, dataset strings and tool output are untrusted. The local operator, Python application, SQLite host, Docker daemon and fixed image are trusted. This is a single-user local portfolio tool, not a hosted hostile multi-tenant execution service.

Runtime controls are fixed: network `none`; UID/GID 65532; read-only root; cap-drop ALL; no-new-privileges; default Docker seccomp; 32 PIDs; 0.5 CPU; 128 MiB memory and equal memory/swap; eight-second wall timeout; aggregate 64,000-byte output limit; 12,000-byte source limit; one immutable read-only input mount; 8 MiB scratch and 1 MiB `/tmp` tmpfs; 1 MiB per-file and 64-open-file limits. The image runtime is readable. Home, secrets, database, other runs and Docker socket are not mounted. There is no host execution fallback or host-writable output mount.

The host supervises pipes with a byte limit and kills/removes the container on timeout or output excess. A random per-call staging directory contains only fixed source and synthetic input. Symlink/traversal/input-size checks precede staging. Known container intents survive process death and are cleaned only by a replacement root owner. Bounded tool retry attempts prevent endless replay.

Acceptance probes must inspect actual Docker HostConfig and execute real IP/DNS, known-existing host/sibling sentinel, UID/capability/rootfs, memory/CPU/PID/tmpfs/output/wall and cleanup tests with positive controls. Merely showing flags or attempting to read an invented nonexistent file is insufficient. These checks demonstrate specific configured controls; they do not prove absence of all kernel/container escapes. Docker shares a kernel. Never use privileged/unconfined runtime containers for this workbench.

BuildKit is a separate trusted build service, not an untrusted runtime. `scripts/build_sandbox.py` creates a project-specific builder and verifies its effective cgroup limit is at most two CPUs/four GiB before building. It refuses missing/excessive limits. On Studio the whole build remains inside the shared heavy-job queue. It never modifies another project's builder.

Local web protections reduce drive-by localhost requests: exact loopback Host, exact same-origin Origin, CSRF, CSP, escaped output, no arbitrary paths/uploads/shell or live-provider selection. This is not an authenticated remote service. Do not bind it publicly or put real credentials/data into questions.

Application evidence checks establish artifact consistency. A malicious program can calculate a wrong number consistently. The ten independent golden expectations, plus a deliberately wrong analysis negative control, address that limitation on the supplied fixture. A reviewer response alone never establishes correctness.
