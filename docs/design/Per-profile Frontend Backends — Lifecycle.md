---
updated: 2026-10-01
status: Sean HDD runtime verified; Syncthing receiver ready; actual Mac pairing pending
---

# Frontend backends: setup and daily use

## Current Sean rollout — 2026-10-01 (supersedes source placement and rollout order below)

Sean requested staying with his profile first and using an agent-facing runbook as the primary onboarding interface. See [[Chart Dev — Agent Onboarding]]; canonical source is `~/workspace/chart-infra/docs/AGENT-ONBOARDING.md`. Repository paths are explicit inputs discovered by the laptop agent, not a required `~/workspace` convention. The helper commands are optional building blocks for inspect/pair/resume/wait/pause.

The three active backend checkouts and their Linux dependency caches now run from `/mnt/hdd/shared-dev/profiles/sean/source/workspaces/pilot/` and `dependencies/`. Verified originals and SSD volumes remain retained. Empty source mirrors for pairing are under `source/mirrors/laptop/`. Only small Syncthing device/config state stays on SSD. Mongo and imported workspaces are unchanged; HDD watcher/data/identity checks passed.

A dedicated `chart-sean/syncthing` receiver is running at `100.66.127.115:22001`, with three receive-only folders **paused and unpaired**. Its identity/config/source volumes survived runtime teardown; exclusions, network isolation, existing OpenScape configuration and API health were verified. Actual Mac pairing, transfer/reconnect tests and activation of laptop mirrors still need to run on the Mac. The current apps continue using the working HDD pilot checkouts.

`chart apps select` now supports explicit prepared source/dependency selection with stopped apps, retained old volumes and guarded inputs. Synced sources also require a matching laptop checkpoint and paused owned folders. App/sync/Mongo teardown are currently separate commands; each retains its data/identity/volumes. Named Mongo dataset switching and additional-developer account/RBAC onboarding remain pending. No second developer stack was provisioned for this step.


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


**Current decision (2026-10-01, supersedes the earlier follow-up):** one Mongo data-bearing member per profile, initialized as replica set rs0; no Mongo TLS, CA, split horizons, SRV or per-member Services. Laptops use IP:port with `directConnection=true`; in-cluster apps use cluster DNS and `replicaSet=rs0`. The single-member pilot and two-profile data/auth/network isolation checks passed. Source: `/home/sean/workspace/chart-infra`; Sean’s offline auth/tharamine/Orange stack is now deployed and browser-verified. See [[Chart Dev — Getting Started]] for the installed commands. Named-dataset switching and multi-developer onboarding remain separate work.


This defines the scripts to build for [[Per-profile Frontend Backends]]. **Sean’s auth/tharamine/Orange pilot is deployed; the ./dev frontend commands below remain proposed syntax.** The standalone chart-infra Mongo commands are installed; see [[Shared Dev — Mongo Pilot]]. The original meta runner manages the Go pipeline. Keep this distinction visible until the new commands pass acceptance tests.

The rule: **teardown stops services and keeps data. Spin-up reuses the same data. Nothing deletes data without an explicit, targeted instruction.**

Review follow-up: [[Per-profile Frontend Backends — Review Follow-up]]. Fable’s review found pilot blockers. Sean has now asked to resolve the remaining choices; settled decisions are recorded there. The design remains broader than the installed pilot; use the implementation update and human guide for current commands and verified scope.

## What is kept

**Dataset decision (2026-10-01):** use one retained Mongo PV/PVC pair per developer profile and separate subdirectories for each named dataset and its member-0, retaining the member-<ordinal> convention for future expansion. This replaces the earlier three-PVC-per-dataset proposal and the database-name-suffix alternative. The same profile claim is reused across dataset changes. Proxmox is deferred; this design uses the existing Ubuntu/k3s host.

Each profile keeps its selected source workspace, Mongo dataset, development keys and protected configuration. Normal teardown also keeps dependency caches for faster startup. An optional **data-only retention** mode removes this capability’s runtime objects and disposable dependency caches, retaining the database PVCs/PVs and the small inventory/credentials needed to reopen them. Source worktrees and shared image/build caches remain owned by their separate tooling. These selections survive shell sessions; store the inventory outside Pods and never regenerate an existing identity on startup.

A Mongo **dataset** is a named set of application data: users, sessions, layouts and other records. On disk it has one member-0 directory within the profile’s one Mongo volume. Example names: `default`, `empty-login-test`, `layout-bug`. Application database names can remain the same across datasets because selecting a dataset selects a different complete Mongo data directory for every member.

Code and Mongo data are independent selections. A fresh code workspace can use existing data. A fresh Mongo dataset can use existing code. Neither operation deletes the previous selection.

## Put Mongo data on the HDD

**Suggested root: `/mnt/hdd/shared-dev/profiles/`.** The host inspection on 2026-09-30 found `/mnt/hdd` already mounted from `/dev/sda1` as ext4, with about **1.7 TiB available**. It has a UUID-based `/etc/fstab` entry. No repartitioning or root-filesystem changes are needed. The existing `/mnt/hdd/shared-dev/backups/` remains separate.

Use this layout for new Mongo datasets, including the currently active one:

```text
/mnt/hdd/shared-dev/profiles/
  sean/
    identity/                         # protected keys/config; outside Mongo PV
      datasets/
        default/                      # inventory + membership/bootstrap state
        empty-login-test/
    mongo/                            # one retained PV/PVC points here
      default/
        member-0/                     # subPath default/member-0 -> /data/db
      empty-login-test/
        member-0/
  alex/
    ...                               # separate profile PV/PVC and directories
```

These are proposed paths, not directories provisioned by this handoff. Dataset names must be validated, with no path traversal or symlink escape. Inventory and credentials stay private outside the Mongo PV root, under the profile identity directory. Operator provisioning must match member-directory ownership to the pinned Mongo image without broadening access to the shared parent or backups. `data new` explicitly creates one empty member-0 directory and its identity marker; `up` and `data use` must never silently recreate a missing directory for an existing dataset.

Use a dedicated `shared-dev-hdd-retain` storage class with `kubernetes.io/no-provisioner`, `WaitForFirstConsumer`, administrator-provisioned local PVs, explicit node affinity and explicit PVC-to-PV binding. Provision one filesystem PV/PVC per developer profile, rooted at that profile’s `mongo/` directory, with PV reclaim policy `Retain`. Use `ReadWriteOnce`, which permits multiple Pods on this same node; do not use `ReadWriteOncePod`. Do not share the claim across developers or namespaces. Dataset subdirectories have no independent Kubernetes quota, retention or snapshot boundary; dataset operations are the runner’s responsibility.

The one-replica StatefulSet references the one pre-created profile PVC through `volumes[].persistentVolumeClaim.claimName`, without `volumeClaimTemplates`. Derive a stable member ordinal from the StatefulSet Pod identity and mount only `<dataset>/member-<ordinal>` at `/data/db`, using `subPathExpr` with validated dataset/member environment values. Verify ordinal expansion on the actual cluster before selecting the final manifest. Distinct member paths are mandatory: two mongod processes must never open the same member directory. The earlier requirement for three distinct PVCs is superseded. A shared external claim is not governed by StatefulSet claim-template retention; preserve it explicitly outside runtime cleanup and avoid runtime owner references that could garbage-collect storage.

Require the expected profile PVC/PV UIDs and dataset/member markers before mongod starts. Verify the paths and pinned-image ownership without recursively changing unrelated dataset ownership. Do not fall back to SSD storage or auto-create replacement empty data. Keep Pod Security `baseline` and PVC mounts rather than Pod `hostPath`. Subdirectories are not an authorization boundary between developers; each profile gets its own PV root and namespace-scoped claim. See [Kubernetes subPath](https://kubernetes.io/docs/concepts/storage/volumes/#using-subpath) and [volume access modes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes).

**Verify the disk before creating directories or starting Mongo.** Require `/mnt/hdd` to be the mounted ext4 filesystem with UUID `14ef1cfa-28ee-4986-8989-2e248896bf07`, and require member paths to resolve beneath that mount. If the HDD is absent, fail; otherwise an ordinary directory at `/mnt/hdd` could silently put data back on the Ubuntu root filesystem. Check HDD free space separately from the existing SSD build guard. Local PV capacity requests are required but do not impose filesystem quotas.

HDD storage trades faster SSD access for capacity. One member stores one copy per selected dataset. Measure startup and interactive query latency; this topology does not protect against losing that disk. Dataset retention is not a separate backup. Container images, logs and source/build caches can still use the Ubuntu filesystem; HDD Mongo placement prevents Mongo datasets themselves from filling it. Existing TimescaleDB/Kafka/Postgres volumes stay where they are; moving them would be a separately planned migration.

## One-time setup

An operator provisions the profile, persistent storage, credentials, development keys, private source mounts and HTTPS route using the approved repository procedures. Keep this separate from daily use; do not ask developers to reissue keys or redo setup every start.

For the proposed per-profile Syncthing workflow, a companion laptop setup step owns device pairing, repository selection, safe additive folder configuration, exclusions on both sides and initial-sync verification. The developer authorizes local API access once; the PC helper configures only their profile instance. Re-runs preserve unrelated sync settings and existing pairing. See [[Per-profile Frontend Backends — Source Sync Options#First-time setup owns pairing and folder configuration]].

The proposed shared runner interface is `./dev frontend <action> --env <profile>`. Operations print the selected profile, source workspace, dataset and URL without secrets. They return a failing exit status on incomplete readiness. Provide `--json` for agents and a read-only `--plan` for mutations.

### Helper access without daily renewal

Sean’s decision: no eight-hour Kubernetes-token expiry workflow. During initial provisioning, install a persistent, revocable credential scoped to that developer profile in private PC-side state. The SSH helper uses it automatically for authorized logs/status/start/stop operations. Keep it outside source synchronization, application mounts and Mongo datasets, retain it through normal teardown, and provide explicit revocation/replacement when required. Never print the credential or issue a shared cluster-admin kubeconfig as the convenience path.

Sean confirmed one separate Linux account per developer for SSH identity, file ownership and private helper state. Provision the developer’s SSH key and configure the helper once; no extra daily login/renewal workflow is introduced. Accounts are unnecessary for calling APIs or connecting Compass. They provide host separation only when directory permissions and helper authorization are configured accordingly; they do not automatically isolate Kubernetes Pods or prevent access to deliberately shared APIs. Syncthing itself does not require separate Linux accounts. Agents stay on laptops; no PC-side AI login is required. This is the future helper contract; existing eight-hour profiles remain unchanged until the runner is updated.

### API and Compass connections

Authorized developers may call teammates’ profile APIs over Tailscale without sharing SSH credentials; application login still applies. API hostname/DNS/HTTPS is a separate design decision.

Mongo uses one plain TCP route per profile in `chart-infra/access.py`. Laptops connect by Tailscale IP:port with `directConnection=true`, using a generated password-bearing URI; no CA, custom resolver, SRV or TLS is required. In-cluster applications use cluster DNS with `replicaSet=rs0`. The current pilot walkthrough is [[Shared Dev — Mongo Pilot]]. A dataset switch closes connections; the saved profile endpoint remains stable and reconnects to the selected dataset.

## Daily use

```sh
# Reuse the selected code and Mongo data; wait for readiness.
./dev frontend up --env sean

# Inspect readiness and follow one backend's logs.
./dev frontend status --env sean
./dev frontend logs --env sean --service orange --follow

# Apply an env/config change to one backend.
./dev frontend restart --env sean --service orange

# Stop the app processes and withdraw their API route; retain Mongo and data.
./dev frontend down --env sean

# Optional: stop this capability's Mongo members too, retaining every volume.
./dev frontend down --env sean --backing

# Optional deeper teardown: remove frontend/Mongo runtime objects and
# disposable per-capability dependency caches, keeping data volumes + identity.
./dev frontend down --env sean --retain data

# Bring it back with the same data and keys. If caches were removed, run prepare.
./dev frontend prepare --env sean
./dev frontend up --env sean
```

`down --retain data` implies stopping Mongo and removes only runtime objects owned by this frontend capability: application/Mongo controllers, the profile Syncthing runtime, Pods, its routes and disposable Jobs/Services. It retains PVC/PV objects, HDD directories, Syncthing device state, protected identity/configuration and inventory. It never deletes the namespace, existing profile Redis/TimescaleDB, shared services or source worktrees. Other named datasets remain on disk with zero running members. Storage retention must not depend on keeping a container, Deployment or StatefulSet alive.

`up` never fetches source, resets a database, regenerates keys or runs live collectors. It can recreate missing runtime controllers from the retained manifest/inventory, while reusing the same Mongo PVC and selected member directories. It must fail if an expected existing volume or member directory is missing, rather than silently creating an empty replacement.

An explicit `prepare` operation installs dependencies from frozen lockfiles and validates configuration. Cache reuse is keyed by workspace/service, lockfile, Node ABI and architecture. If preparation is stale, `up` gives the exact preparation command. Migrations use the owning repository's supported procedure; fail on incompatible schema/code rather than automatically running down migrations.

## Edit files and trigger hot reload

**Keep the developer’s existing cross-repo agent session as the only agent.** The preferred proposal keeps all edited repositories on the laptop and syncs selected backend source to the PC using one Syncthing instance per profile. SSH handles remote tests, logs and lifecycle in the same conversation. No second Claude Code/Codex process or remote AI login is required. The frontend stack, sync onboarding and helper remain proposed, not installed.

### Preferred proposal: local agent edits, Syncthing transfers source

1. Run the one-time laptop setup to pair the devices and register repo folders with safe exclusions.
2. Keep the existing CLI agent in the laptop workspace, using normal file tools and Git across all repositories.
3. Start the profile and verify synchronization is ready. Saving backend source transfers it into the PC mirror and triggers the watcher.
4. Use the SSH helper for tests, logs and lifecycle; wait for sync completion before claiming tests cover the current edit.
5. Keep the PC mirror read-only to applications and do not independently patch it while sync owns the source. Persist Syncthing identity/config across teardown so later starts reuse pairing.

See [[Per-profile Frontend Backends — Source Sync Options]] for the proposed per-profile layout and automated onboarding. This is the review proposal; no Syncthing settings have been changed.

### Alternative: the existing agent uses SSH for PC files

1. Onboard an SSH login/key and an explicit mapping from profile and repository name to the authorized PC workspace path. The runner must report these paths and selected revisions. Use one authoritative checkout per repository for the task; distinguish laptop paths from PC paths in the handoff.
2. Keep using the same CLI agent and conversation that owns the cross-repo change. It edits laptop repositories using its normal local tools.
3. For a repository on the PC, the same agent invokes SSH through its command tool to search/read files, submit patches, run tests and inspect diffs. Command output returns to the same conversation. Native file tools on the laptop still address the laptop; a remote path in a prompt alone does not redirect them.
4. Start the profile’s backends once using the remote runner. Changes to watched files in the profile’s mounted PC workspace then restart the affected backend automatically.
5. The same agent checks remote logs/tests and laptop frontend behavior, reviews both sets of diffs and completes the cross-repo task. No second agent login, session or context handoff is required.

Example of a read-only command issued by the existing agent, once the account and workspace exist:

```sh
ssh alex@100.66.127.115 \
  'cd /path/to/profile-workspace/orange-v2-backend && git diff --stat'
```

Paths and `alex` are onboarding placeholders. The selected PC worktrees must remain the directories mounted by the application Pods. Remote changes to those files are visible even when the application source mounts are read-only; the host-side file writer has separate permissions.

```text
One existing agent session
  ├─ local file tools -> laptop frontend/library checkout
  └─ SSH commands    -> profile backend checkout on PC
                          -> mounted source -> watcher restart
       remote results return to the same agent conversation
```

This direct-SSH alternative requires SSH access, profile ownership and remote development tools, **not an AI CLI or AI-provider login on the PC**. The agent’s model connection and conversation remain wherever that existing CLI runs. Git/private package authentication for PC builds is a separate prerequisite. Keep application offline policies and disabled vendor workers unchanged.

### Reusable remote helper to implement

Add a thin laptop-side SSH helper to the shared tooling so agents do not have to reconstruct hostnames, paths and quoting for every operation. In the preferred sync mode, use it for lifecycle/tests/logs/status and disable source-patch operations against sync-owned mirrors. This is a command adapter, not another agent or an agent-delegation service. Provide profile/repository selection plus remote read/search, patch, command/test, diff, status and logs operations. It must:

- Resolve the registered profile, SSH account, PC workspace and repository, and print which host/path is targeted. Preserve existing agent approval settings; do not disable them to make SSH work.
- Return stdout/stderr and exit status to the invoking CLI. Offer structured status/path output for agent use.
- Transfer patch content over stdin or a protected temporary file, check it against current source before applying, and report conflicts without resetting worktrees. Quote paths/arguments safely and keep code or secrets out of interpolated shell strings. Reject file operations that escape the registered source root.
- Support create/edit/rename/delete of explicitly targeted source files while keeping datasets, private configuration and other profiles outside those file operations. Never use broad cleanup or automatic Git reset.
- Allow tests through the documented host or development-container toolchain. Tools installed only inside a Pod are not automatically available in an SSH shell.
- Leave source fetching, dependency preparation, deployment, data selection and data deletion as separate explicit operations. No pull/build/deploy per ordinary watched-source edit.

Each developer gets their own Linux account for file ownership and SSH access; one account can own several profiles. Set private permissions on workspace/configuration roots and bind helper access to the developer’s authorized profiles. Agent credentials need not be stored on the PC. Teammate API access does not grant access to another developer’s SSH account or private files. Source editing does not require sudo, Docker-group membership or cluster-admin.

The agent must still load meta_repo instructions and the atlas-v2 protocol, then edit the selected feature workspaces. For concurrent independent tasks, use separate worktrees and deliberately choose which one a profile runs. A cross-repo dependency change may require explicit package preparation/rebuild; watcher restart alone cannot update a separately built library or changed lockfile.

### Optional human remote editing

In direct-SSH source mode, a developer may open the PC workspace in a remote editor such as VS Code Remote - SSH, or edit through a terminal over SSH. Do not independently edit a source mirror owned by the preferred sync mode. Saves in direct-SSH mode follow the same source-mount/watch path. This does not require moving the ongoing CLI-agent session. [VS Code Remote - SSH](https://code.visualstudio.com/docs/remote/ssh)

### Optional agent execution on the PC

SSH plus tmux plus a remotely launched CLI remains available for someone who deliberately wants the entire agent session there. It is **not required or the default**, and it must not replace/split an existing cross-repo session. That optional mode needs its own agent installation and per-developer provider authentication on the PC. Existing Sean installations are not an onboarding requirement for the preferred local-agent/sync workflow.

### Synchronization behavior

See [[Per-profile Frontend Backends — Source Sync Options]] for the proposed per-profile Syncthing design, first-time pairing and alternatives. It remains a proposal for review; no source-sync tool has been configured.

Provide an explicit one-way laptop-to-PC sync mode for the preferred workflow. It must target a dedicated profile/service source directory registered with the runner, with the laptop as the sole source of edits. Do not synchronize into a worktree also being edited remotely. Display the destination and source mode in profile status.

In the preferred mode, Syncthing transfers source through its authenticated TCP connection over Tailscale; SSH is used for helper commands. The existing container mount and watcher then see the transferred files. SSH file transfer applies only to the Mutagen/rsync alternatives. Support initial copy plus incremental create/change/rename/delete events, with a source-only preview before the first transfer. Exclude `.git`, `.env*`, signing/private keys, `.npmrc`, `node_modules`, build output and generated caches. Source deletion propagation must be confined to the registered sync tree and leave excluded paths protected; it must never reach dataset/configuration directories. Never upload macOS dependencies into Linux containers. Batch changes sufficiently to avoid a restart for every file in a large transfer, and show sync failures/disconnection rather than claiming the remote code is current.

The first proposed acceptance path keeps one cross-repo agent editing laptop checkouts and mirrors selected backend source onto the PC; do not also patch its remote mirror independently. The sync setup helper is not installed yet. Editing local files alone, or pushing to GitLab alone, will not trigger the remote watcher; deployments never pull automatically. An explicit Git update on the PC can update code too, but stop watchers around multi-file branch changes and prepare dependencies when required.

### Changes that need more than a save

| Change | Action |
| --- | --- |
| Watched backend source | Save remotely, or complete the explicit sync; watcher restarts that backend |
| Env/configuration or keys | Update through the profile’s approved configuration workflow, then explicitly restart the backend |
| Dependency manifest/lockfile | Run profile `prepare`, then restart affected services |
| Node version/system packages | Rebuild the development runtime image and recreate affected workloads |
| Laptop frontend source | Use the laptop Vite development loop |

The watcher normally follows imported files; dynamically loaded files/assets may need explicit include patterns. This restarts the backend process, can interrupt requests/WebSockets, and preserves Mongo data. [tsx watch mode](https://tsx.is/watch-mode)

Acceptance must prove one existing agent session can edit frontend/backend repositories locally, synchronize the backend edit, receive local and remote test results, and observe the backend change with unchanged image digest and Pod UID. Test direct SSH source operations separately only if supporting that alternative. Test editor atomic-save/rename behavior and any supported sync mode; only choose a verified polling fallback if native events fail. Source ownership stays per profile, source workspaces can remain on SSD, and Mongo datasets remain on the HDD.

## Start with clean code

In sync mode, the developer explicitly selects or creates a clean laptop branch/worktree using existing source tooling. Preserve the previous worktree and all dirty changes. One registered PC mirror per profile/repo receives the selected laptop source; do not run Git branch/reset commands or `source prepare --workspace --ref` against that mirror.

For an incompatible branch/dependency change: stop affected apps, explicitly update the approved laptop folder mapping without clobbering existing sync settings, complete the reviewed sync checkpoint, prepare Linux dependencies/migrations as needed, then start. Record laptop HEAD/dirty state plus the transferred content fingerprint. None of this changes the selected Mongo data or keys, and no fetch is implicit.

Direct-SSH source mode may separately use meta-managed PC worktrees, with sync disabled for that target. The helper must report which mode is active and reject source commands intended for the other mode.

## Start with empty Mongo data, keeping the old data

```sh
./dev frontend data list --env sean

# Create an empty dataset; do not alter the current one.
./dev frontend data new --env sean --name empty-login-test

# Stop this profile's apps and all Mongo members; select the dataset while down.
./dev frontend data use --env sean --name empty-login-test
./dev frontend up --env sean

# Return to the previous data later.
./dev frontend down --env sean
./dev frontend data use --env sean --name default
./dev frontend up --env sean
```

Use one Mongo StatefulSet and headless Service per profile, with stable in-cluster member DNS and external profile IP:port reused across dataset selections. Only one dataset runs in that profile at a time. Every dataset has one retained member-0 directory on the same profile PVC, plus protected inventory recording relative paths, member markers, shared PVC/PV UIDs, replica-set identity/configuration, schema/migration state and pinned Mongo version. Never attach old database files to an incompatible Mongo version. Reuse existing replica-set configuration on restart; bootstrap only a verified new empty set.

`data use` acquires the profile lifecycle lock, validates the target, stops application writers and withdraws the route, scales Mongo to zero and waits for the member Pod to terminate. It then updates the StatefulSet mount-path selection while still at zero replicas and records the selected dataset. It leaves services stopped; the following `up` starts the single member against that dataset, verifies member markers, waits for one PRIMARY, and only then starts apps and publishes the route. Do not change a running Pod directly or use a rolling update for dataset switching: it would mix datasets within one replica set. Recheck HDD identity and directories at startup; protect automatic Pod restarts as well as helper-driven starts from initializing an empty replacement.

Keep profile-level signing keys stable. Keep Mongo membership credentials and replica-set identities associated with their dataset. Stop apps and remove their route before changing database configuration; start them only after Mongo election and readiness succeed. Serialize lifecycle operations per profile across developers and agents, including different host logins. A failed switch must leave enough inventory to resume or restore the previous selection.

Fresh-data operations apply only to this frontend capability's Mongo storage. They must not reset TimescaleDB, metadata Postgres, Kafka, another profile, or source directories. Namespace cache keys by dataset, or explicitly design equivalent separation, so switching cannot serve cached records from a previous dataset. The current Dragonfly is a disposable, non-persistent cache; any feature requiring durable Redis records needs persistence work before it joins this guarantee.

An empty dataset may receive only explicitly selected, idempotent synthetic fixtures. Do not silently run destructive test setup or import production users/sessions.

## Delete data only when explicitly requested

There is no deletion flag on `up`, `down` or source preparation. No age-based garbage collection removes datasets or keys. Disposable dependency caches may be removed only by the explicitly selected data-only teardown; required identity/configuration is never treated as a cache.

A future deletion command must first show the exact profile, inactive dataset, shared PVC/PV identities, member-0 path and associated private metadata paths. Execution requires a matching explicit confirmation such as `--confirm-delete sean/empty-login-test`. Hold the profile lifecycle lock, reject the active dataset and any consumers of the target directories, and validate ownership against inventory, markers and volume UIDs. Delete only that dataset’s files and associated inventory/credentials. Never delete the shared profile PVC/PV, the Mongo root or sibling datasets, and never use a broad namespace/name-prefix deletion. Removing the whole profile volume is a separate explicitly authorized operation; `Retain` alone does not protect files from deletion inside a mounted volume. This contract does not authorize deleting any current data.

## Proof required before calling the scripts ready

1. Create a synthetic user and layout; app-only teardown/spin-up restores them with unchanged keys and PVC UIDs.
2. Run data-only teardown, verify there are no frontend/Mongo runtime controllers or Pods left, and verify all HDD files, PVC/PV UIDs and keys remain. Recreate runtime objects against those same volumes; the same records return.
3. Select clean code while preserving a dirty previous checkout and all Mongo records.
4. Create dataset B on the same PVC, verify it does not contain dataset A's records, then select A and recover them. Confirm unchanged PVC/PV UIDs, the selected member-0 mount path, and no overlap of running members from different datasets. Repeat to prove reuse.
5. Repeat operations, simulate an interrupted switch and attempt concurrent operations; preserve data and report recoverable state without ambiguous selection.
6. Prove profile B cannot select, mount or delete profile A's storage. On an explicitly authorized throwaway dataset, prove targeted deletion preserves the shared PVC/PV and every sibling dataset. No destructive test may target other data.
7. Prove new Mongo writes are on the HDD rather than the root filesystem. Simulate an absent/wrong HDD, missing member directory and mismatched marker in a safe test harness; require failure before mongod starts or any replacement data directory is created. Verify this for both helper starts and automatic Pod restarts.
8. Report actual CPU/memory/disk use. Do not introduce Kubernetes CPU/memory requests, limits, profile quotas or default limits; keep required PVC storage requests.
