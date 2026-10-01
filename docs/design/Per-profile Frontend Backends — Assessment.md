---
updated: 2026-10-01
status: Sean offline app pilot verified; multi-developer onboarding and dataset/source switching pending
---

# Per-profile frontend backends — assessment

## Implemented pilot update — 2026-10-01

Sean’s stack is available at `http://100.66.127.115:13000`; Mongo is `100.66.127.115:27018`. The human walkthrough is [[Chart Dev — Getting Started]]. Canonical code and detailed evidence: `~/workspace/chart-infra/docs/DEPLOYMENT.md`; explicit source/build/dependency/bootstrap commands: `docs/PREPARATION.md`.

- Verified: private npm installs, backend/database health, browser email-code login and refresh, workspace save/restore after full app+Mongo runtime recreation, all three source watchers, database credential boundaries, private-layout read denial and peer workspace-edit denial. Two-profile Mongo isolation was verified earlier; only one full application profile is deployed.
- No backend production `.env` or dotenvx keys are used. Generated protected development configuration is injected at startup. Laptop Vite uses the small `.env.local` in the guide plus the offline wrapper; actual laptop frontend verification is still pending.
- The API uses a plain Tailscale-only TCP route at port 13000. Localhost Vite proxies browser API calls and keeps Secure cookies on the local origin. No API DNS, HTTPS certificate or Traefik change is required for this pilot. Separate browser profiles avoid mixing localhost sessions when targeting teammates’ APIs.
- App Dragonfly is a separate disposable cache in `chart-sean`, leaving the Go profile cache untouched. Persistent data/identities remain on HDD; source/dependency caches are on SSD. No CPU/memory requests or limits.
- Implemented `./chart apps down --data-only` removes app runtime while retaining data, identity, all PV/PVCs, source and dependency caches. Mongo has its own matching lifecycle. Nothing deletes datasets. Proposed cache-pruning behavior below is not implemented.
- Source fetch/install/deploy remain explicit separate commands. TypeScript edits hot reload, but switching an existing profile to a new workspace/runtime/dependency generation is not implemented. Dependency-input changes fail closed. Syncthing, separate developer accounts/RBAC, pairing and named-dataset switching remain planned; the existing Syncthing configuration is untouched.
- The offline wrapper and Pod policies block live integrations. Chart/market-data integration, email delivery, external auth, billing and notifications are unavailable in this milestone. Temporary browser verification resources were removed; Sean’s application stack remains running.

The remaining sections retain design/evaluation context. Proposed `./dev frontend` syntax is not an installed interface; use the human guide’s `./chart` commands for the implemented subset. The original Fable review remains unchanged.


**Yes, this is possible on the existing PC.** The proposal in [[Per-profile Frontend Backends]] is a workable design, but implementing it is a small development-platform project rather than simply deploying three Node containers. The main work is application bootstrap, safe source mounts, HTTPS/browser configuration and lifecycle integration.

Start with Sean and then prove a second profile. Do not provision all four profiles until the first pair has passed browser and resource tests.

Review follow-up: [[Per-profile Frontend Backends — Review Follow-up]]. Fable’s review found pilot blockers. Sean has now asked to resolve the remaining choices; settled decisions are recorded there. The design remains broader than the installed pilot; use the implementation update and human guide for current commands and verified scope.

## What I verified

During the initial evaluation I inspected the running development cluster and fetched separate, read-only source snapshots for review. That evaluation did not change cluster resources, running services, credentials or existing worktrees. The subsequent per-profile resource-policy change is recorded separately in the shared-dev deployment evidence. Dependencies were not installed and no deployed blk/production endpoint was contacted.

- The node has approximately 49 GiB available RAM and 602 GiB free SSD space. Current Kubernetes metrics show approximately 10.6 GiB memory use and 0.35 CPU. These are an idle snapshot, not a capacity benchmark; swap already has about 2.9 GiB in use.
- Four developer namespaces already exist: Sean, Alex, Ryan and Gerald. Each contains its own TimescaleDB and Dragonfly. Existing application workloads are stopped.
- At initial inspection each profile had a 12 GiB/12 CPU limit quota and a 70 GiB requested-storage quota. Sean subsequently requested removal of profile Kubernetes CPU/memory requests, limits and quota defaults. Capacity planning below follows that decision; mandatory PVC storage requests remain.
- Traefik 3.7.8 already runs. Its LoadBalancer service advertises the LAN address, and its ServiceLB DaemonSet claims host ports 80 and 443. No current Ingress/IngressRoute resources were found.
- Profile namespaces enforce Pod Security `baseline` and default-deny networking. Their developer Roles do not grant Ingress or PVC creation.
- The three requested backend repos are not yet registered in the existing `repos.conf`.
- Current source still pins Node v24.20.0 and uses `dotenvx` plus `tsx watch`; the Node 24.20.0 Debian slim image manifest is available. Auth's README requires pnpm 11. An exact pnpm release still needs pinning and installation verification.

Reviewed source revisions are newer than the handoff's anchors:

| Repository | Reviewed main revision |
| --- | --- |
| auth-service-backend | `f680833f49da7123d04d122acc87b3b61ff20d15` |
| tharamine-user-service | `a9b4cad33fb038a0585db474211e1982bdbe28e8` |
| orange-v2-backend | `1033e2cba3d01a381ad4d8c634713325c66b837b` |

The frontend checkout and `script-migration` were not inspected in this evaluation. Frontend proxy/token behavior remains a handoff assertion requiring verification against the selected frontend revision.

## Required changes to the implementation plan

**Latest design direction for review:** per-profile Syncthing mirrors laptop backend source, with first-time setup handling pairing and exclusions. The existing cross-repo agent stays on the laptop. Direct SSH source patching and Mutagen are alternatives; the SSH helper is still needed for tests/logs/lifecycle. No new source sync has been deployed.

### 1. Resolve the source mount policy before building the watcher deployments

Direct `hostPath` mounts are forbidden by the profile namespaces' current `baseline` policy. An administrator creating the same Pod is not, by itself, a solution to that admission rule. [Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)

Preferred approach to validate: administrator-provisioned local PersistentVolumes for explicitly approved checkout directories, bound to profile-scoped PVCs, with application mounts read-only. This retains baseline Pod policy and avoids granting developers arbitrary host mounts. Keep writable dependencies and caches in separate volumes. Test ownership, nested mounts, source-change events and dependency installation against the real worktrees. A PVC is not a substitute for carefully controlling which host path the administrator assigns.

Keep source preparation separate from application startup. A watcher uses files on this PC; editing a laptop checkout needs SSH/remote IDE access or explicit one-way synchronization. Git operations must not silently replace a checkout used by a running watcher. Keep the developer’s existing cross-repo CLI agent session. Its local tools edit all repos, with the proposed per-profile Syncthing transferring backend source into PC mirrors. A reusable SSH helper resolves profile/repo paths for tests/logs/lifecycle and returns results to that same conversation; it must not launch another agent or independently patch a sync-owned mirror. Exact onboarding and the proposed one-way sync workflow are specified in [[Per-profile Frontend Backends — Lifecycle#Edit files and trigger hot reload]]. Prove one agent can edit the local frontend/backend repos, observe completed source transfer, and watched edits restart each backend without changing its image or Pod UID.

### 2. Resolve the existing ingress exposure before claiming Tailscale-only HTTPS

The proposed host-to-profile routing is straightforward HTTP routing. It is independent of the deferred single-port TimescaleDB proposal.

However, the existing ServiceLB already uses host ports 80/443, and the existing Traefik HTTPS service is exposed on the LAN. Adding a Tailscale-bound frontend to that same service does not remove its alternate LAN/NodePort path. HostPort forwarding also has to be reconciled with any new host process binding 443. [K3s networking services](https://docs.k3s.io/networking/networking-services)

Fable’s review removes the competing HAProxy-on-443 proposal. Evaluate using the existing Traefik listener directly, with source restrictions proven on a scratch Service and all LAN/NodePort access paths checked. Sean has confirmed Tailscale-only exposure; enforcement still needs proving. Do not globally disable ServiceLB or affect Minecraft Services.

Provision exact profile host rules, TLS certificates and certificate renewal. Keep hostname allocation and ingress mutations administrator-controlled. Add narrowly scoped NetworkPolicies permitting Traefik to reach Orange, and test both unknown-host rejection and denial through unintended network paths.

### 3. Treat dependency installation as a separate authenticated job

Register the backend repos with meta tooling and create/select profile-owned worktrees. Include the owning migration repository when seeding is needed, and identify the frontend revision used for acceptance.

Use a pinned development image and frozen lockfiles. Mount private package credentials only during installation, separate from runtime application mounts. No host `~/.npmrc` or pnpm user config was found in the usual locations; private package access has **not** been verified. Existing GitLab SSH/Go authentication does not prove npm registry access.

Key dependency reuse by service, workspace, lockfile, Node ABI and architecture. Account for Husky prepare hooks and linked worktree `.git` paths; installation must not require writable access to another checkout's Git metadata. Audit lifecycle scripts and native builds before relying on a cache populated outside the runtime image.

### 4. Implement the local identity and account bootstrap deliberately

Generate persistent per-profile user-JWT and service-auth identities, configure their public-key registries, and preserve keys across ordinary restarts. Auth requires Mongo configuration, local PEM files, a valid local-key YAML configuration and its other boot secrets. Orange attempts service-token warmup before it starts listening, so upstream auth must actually work.

A useful finding in current auth source: the email-code login flow logs the code in local mode, **but still calls the SES/Resend email service**. The send error is caught, but `NODE_ENV=development` is not an offline mail adapter. Fable proposes forcing SES to a closed loopback endpoint with dummy credentials, metadata lookup disabled and one attempt. Treat this as a candidate: verify that the pinned SDK honours the endpoint and prove bounded request latency before calling the login path offline. Otherwise use an explicit local mail adapter or another proven browser-supported login path. Do not count an API-only password login as proof that the actual frontend can sign in.

Fixture/bootstrap work belongs in `script-migration`, per the auth and tharamine repository rules. Do not execute their test setup against persistent development databases: it includes destructive test preparation.

The repository instructions also reserve Kubernetes Secret mutations for a human operator. Plan an explicit operator provisioning step with prepared manifests/commands; ordinary frontend start/stop should then reuse the provisioned identities. This evaluation does not request or perform that step.

### 5. Audit every startup worker, not just two flags

The handoff correctly calls out Orange's marketplace outbox and tharamine's follower-email defaults. Current tharamine startup also starts several room/notification/attachment workers, voice capacity, canvas maintenance, Telegram bootstrap and alert reconcilers; their individual guards need checking. Orange additionally has market-event ingestion and an entitlement reconciler.

Create a reviewed offline configuration for the selected revisions. Keep namespace egress denied, and distinguish workers disabled in configuration from workers repeatedly failing against blocked networks. Dependency installers may need approved registry access; application Pods should not inherit that allowance.

### 6. Budget Mongo and dependency storage explicitly

Sean’s final 2026-10-01 decision is one data-bearing Mongo member per profile, initialized as rs0. It supersedes the earlier three-member requirement. Transactions and change streams passed in the new pilot; there is no secondary or member failover. Keep the ordinal directory layout for explicitly planned future expansion, without automatically reconfiguring an existing set.

Do not add Kubernetes CPU/memory requests or limits, profile ResourceQuotas or LimitRange defaults. Compare actual idle, startup, dependency-install and active-use measurements as profiles are enabled. Profiles compete for available capacity, so memory pressure in one can affect others; the old sum-of-limits quota calculation is no longer a capacity criterion.

Per Sean’s follow-up, put new Mongo datasets on the existing HDD under `/mnt/hdd/shared-dev/profiles/<profile>/mongo/<dataset>/`, using one retained local PV/PVC per profile rooted at `mongo/`, with `<dataset>/member-0` mounted through `subPath`/`subPathExpr`. The single-node claim uses `ReadWriteOnce`; switching datasets stops all members before changing mount paths, while every dataset directory remains retained. Database-name suffixing and separate PVCs per dataset are superseded by this decision. About 1.7 TiB was available there at inspection. Fail if the expected HDD is not mounted; never silently write these datasets onto the Ubuntu root filesystem. Keep required PVC storage requests, but monitor actual disk consumption and retain both HDD and SSD free headroom. Local-path request sizes do not cap writes or preallocate the advertised disk space. Tune WiredTiger cache per member separately from Kubernetes process limits, allowing memory outside its cache.

## What to build, in order

1. **Source and runtime preparation:** register repos; choose revisions; verify package access; build the pinned dev image; settle source mounts, dependency caches and actual-usage monitoring.
2. **Mongo backing:** one-member StatefulSet initialized as rs0, one retained HDD PV/PVC with dataset/member-0 mapping, keyfile authentication and per-profile users. Plain Tailscale IP:port access uses directConnection=true; cluster clients use replicaSet=rs0 and cluster DNS. No Mongo TLS/CA/SNI/SRV/resolver setup. The isolated pilot’s transaction, change-stream, retention and two-profile isolation checks passed; actual Compass remains a client check.
3. **Sean's application stack:** persistent development keys/config, offline worker settings, approved account/bootstrap fixture, then auth → tharamine → Orange with meaningful readiness checks.
4. **Browser access:** reconcile Traefik exposure, provision hostname/certificate, configure laptop CA trust and Vite proxy, and prove sign-in, refresh and layout save/reload in a real browser. Authorized teammates must be able to use a profile API without sharing SSH credentials; validate certificate trust and isolated browser sessions accordingly.
5. **Runner lifecycle:** implement the reusable command contract in [[Per-profile Frontend Backends — Lifecycle]]: provision/prepare/up/status/logs/restart/down, optional backing suspension, and independent code/data selection. Withdraw the frontend route on stop. Preserve sources, keys and Mongo datasets through teardown/spin-up; a fresh dataset must retain the previous one for reuse. Offer data-only retention to remove runtime objects and disposable dependency caches while keeping HDD volumes plus protected identity/inventory. This frontend work remains a handoff/design task, not a deployed capability.
6. **Second profile and isolation:** independent users/keys/Mongo/Redis, source-edit watcher proof for each service, peer-network/credential denial, and restart persistence. Prove a fresh Mongo dataset and switching back to the original without deleting either. A deletion test requires explicit authorization for the named disposable dataset; never target an existing TimescaleDB PVC, source checkout or the whole namespace.
7. **Market-data acceptance:** inspect how the selected frontend chooses its market-data token after login. Then use either an explicitly approved dev identity with the remote endpoints or a profile-local gateway/live-feed using approved local trust and offline fixtures. This evaluation made no live API calls.

The existing Dragonfly runs in cache mode without persistent storage. Durable users/layouts must reside in Mongo. If the selected frontend capability requires durable Redis records, implement persistence before claiming full runtime-teardown continuity; ordinary cache contents are not currently durable.

## What can and cannot be promised yet

**Feasible:** isolated persistent Mongo; Node source watching without image rebuilds; profile URLs on one HTTPS port; local user sessions/layouts; reuse of profile Redis; start/stop and restart persistence.

**Not yet demonstrated:** private package installation, exact browser-compatible synthetic-user bootstrap, runtime worker shutdown, trusted browser cookies/refresh, watcher behavior through the chosen mounts, measured multi-profile capacity, and market-data access after local login.

A locally signed user JWT will not automatically be accepted by deployed blk/ore. Login/layout success and chart/live-data success must remain separate acceptance results until the frontend token path and backend trust are verified. A fully offline local gateway is consistent with the original shared-dev constraints; changing remote trust or using live credentials is a separate decision.

**Recommendation:** proceed with the architecture after resolving mounts, ingress exposure and offline login. Keep the first milestone to one profile completing login → save layout → stop/start → reload layout. Add the second profile and the market-data integration only after that path works.

## Evidence locations

Read-only review snapshots are under:

```text
/home/sean/workspace/meta_repo/zmeta_docs/frontend-backends-evaluation/
```

Key source anchors in those snapshots:

- All three `package.json` files; auth `README.md` and `src/tests/setup.ts`.
- Auth `src/services/login/login-code.service.ts:112` and `src/services/email.service.ts`.
- Auth `src/utils/jwt-parser.util.ts`, `src/utils/service-auth-parser.util.ts` and `docs/development/operations.md`.
- Orange `src/app.ts:435` and tharamine `src/app.ts:260`.
- Auth/tharamine `docs/development/architecture.md`, “Scripts and migrations”; all three `AGENTS.md` files for Secret operations.
- Runner `backing.py` (namespace policies/quotas), `common.py` (RBAC), `dev` (release and stop behavior), and `access.py` (current TCP exposure).
