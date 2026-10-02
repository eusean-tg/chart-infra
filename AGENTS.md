# Chart-infra agent instructions

Read [README.md](README.md) and [docs/OPERATIONS.md](docs/OPERATIONS.md). Use [docs/AGENT-ONBOARDING.md](docs/AGENT-ONBOARDING.md) for source synchronization and development configuration imports. The reusable agent entry point is [skills/chart-infra/SKILL.md](skills/chart-infra/SKILL.md).

For the parallel Incus pilot, read [incus/README.md](incus/README.md). `./chart`, profile registration and the source-activation protocol below apply to k3s. Incus preparation uses its own host config and retained box marker; it does not replace existing profiles or sync sessions. Source/dependencies for Incus reside on the bounded SSD pool, with datasets and identities on the verified HDD. Use Mutagen over box SSH. Keep host Incus administration with the operator; developer root is confined to their unprivileged box.

## Operating boundaries

- Target only the registered shared development PC and cluster. Preserve host, HDD, ownership, data-marker and volume-UID checks. Use the corresponding lifecycle locks for mutations.
- Pass `--profile` explicitly. Run source-sync operations as the profile's registered Linux owner. Developer account and scoped Kubernetes-access provisioning require separate operator work; do not distribute another user's SSH credentials or cluster-admin kubeconfig.
- Keep source fetching, source synchronization, dependency installation, configuration selection and deployment separate. Do not start vendor collectors, metadata refreshes or history downloads. Any future live capture needs a cluster-enforced deadline.
- Keep endpoints Tailscale-only and preserve unrelated workloads, the Go pipeline, existing ingress and other synchronization tools.
- Configure no CPU/memory requests, limits or namespace resource quotas. Storage capacity metadata and explicit database cache tuning are separate.
- Retain data, identities, imports, source, caches, PVs and PVCs through teardown. Delete none without an explicit instruction naming the target. Never initialize replacement data during startup or bypass a missing/replaced disk, marker or volume guard.
- Preserve existing keys, database users and replica-set identity. Do not force-reconfigure Mongo or rotate development identities as a troubleshooting shortcut. Pin image digests and review runtime/dependency identity together.
- Use one Mongo data-bearing member per profile, replica set `rs0`, keyfile authentication and profile/service-specific users. Laptops use IP:port with `directConnection=true`; Pods use cluster DNS and `replicaSet=rs0`. API-hostname DNS is independent of Mongo.

## Source and configuration

- Keep k3s backend checkouts, mirrors and Linux dependency caches on the verified HDD. Use the SSD source/cache paths for Incus boxes. Do not edit laptop-owned mirrors or apply local backend patches.
- Use the matching `laptop-sync.py` and `sync_common.py` with the pinned Mutagen version. Discover laptop paths explicitly. Preserve unrelated sessions and synchronization configurations; operate only on recorded owned session IDs.
- Freeze laptop sessions before installing dependencies or selecting synced source. A valid freeze flushes, pauses owned sessions and verifies matching fingerprints. Plain pause is insufficient. Do not forge checkpoints or resume through raw Mutagen commands.
- Keep runtime source, dependency and configuration mounts read-only. Use explicit workspace selection while apps are stopped; retain prior generations for rollback.
- Keep private keys, tokens, dotenv files, connection URIs and runtime evidence outside Git and source synchronization. Never print secret values or raw private config diffs.
- Permit development `.env.local` imports through the agent procedure in the onboarding guide. Do not add an importer script or replace runtime config wholesale. Preserve profile endpoints, identities and offline restrictions; retain private backups and verify the requested feature.
- Leave Mutagen daemon login registration opt-in. Coordinate daemon-wide operations with every workflow using that daemon. Do not silently change automatic startup preferences.

## Documentation and verification

- Apply `definitive-docs` to maintained prose and comments. State verified behavior and concrete limits; keep rollout narration out of operating reference.
- Keep reusable chart-infra instructions in this repository. Put host-specific inventory, troubleshooting investigations, reviews, acceptance evidence and roadmap items in `/home/sean/obsidian/vault/Chart Infra/`, following its index and knowledge-base conventions.
- Record automated, agent-observed and developer-reported checks distinctly. PC driver results do not establish actual laptop Compass or browser acceptance.
- Run checks appropriate to the changed behavior. Inspect integration-test scope before execution: some tests write fixture records or create cluster objects. Never reset source or production-like datasets to make a test pass.
- Keep named datasets, Linux-account/RBAC provisioning and second full-profile acceptance explicitly unimplemented until their code and verification exist.
