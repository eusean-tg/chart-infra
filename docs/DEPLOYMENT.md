# Deployment record — 2026-10-01, single-member revision

Sean’s final decisions supersede the earlier three-member/TLS/SNI design. Each profile now has one data-bearing Mongo member initialized with `rs.initiate`, one static HDD PV/PVC, and one plain TCP route in `chart-infra/access.py`.

## Active pilot

- Profile: `mongo-pilot-single`; namespace `chart-mongo-pilot-single`.
- StatefulSet: `mongo`, replicas 1; member `mongo-0.mongo.chart-mongo-pilot-single.svc.cluster.local:27017`, replica set `rs0`. No split horizons.
- Services: headless `mongo` for cluster member identity and one `mongo-access` ClusterIP for the profile’s external route. No per-member external Services, NodePorts or LoadBalancers.
- External route: `100.66.127.115:27017` to that ClusterIP. Docker proxy `chart-infra-access`, source ACL `100.64.0.0/10`, Tailscale IP bind only. Plain TCP; no TLS/SNI or CA.
- Clients: laptops use IP:port and `directConnection=true`; in-cluster applications use cluster DNS with `replicaSet=rs0`.
- Auth: persistent generated membership keyfile, admin identity and distinct per-profile read/write user for database `chart`. No production credentials.
- Namespace enforces restricted Pod Security. Mongo runs UID/GID 1000, read-only root, dropped capabilities, no API token/host networking/direct hostPath. Default ingress/egress restrictions permit the selected same-namespace Mongo traffic and cluster DNS. The host-network Docker proxy uses explicit bind/source ACLs.

The profile `mongo-isolation-check` (port 27022) was created only to verify isolation, then stopped with data-only teardown. Its PVC/PV, identity and synthetic data remain retained. At the Mongo-only milestone actual developer frontend Mongo deployments had not started; Sean’s subsequent application pilot is recorded below.

## Exact versions

- Host: `sean-trontal`, Ubuntu 26.04, k3s `v1.36.3+k3s1`, Intel x86_64.
- MongoDB: `7.0.43`, mongosh `2.10.0`.
- Mongo image: `docker.io/library/mongo@sha256:609d76574151bee8b148caee83cd9fec8b5f695e5a6db9f1e0f9dced0673574e`.
- HAProxy: `3.2.23-1feef49`.
- Proxy image: `haproxy@sha256:6343ce34a132a5dceaa24767d739df2bd519f8f7c1079ae39e4821334e8eb42e`.

Mongo 7.0.43 was the official `mongo:7.0` linux/amd64 image resolved during this implementation. It is the tested pin, not a claim of the newest upstream release; 7.0.45 was listed upstream but not selected from that image tag.

## Storage and resources

HDD `/mnt/hdd`, ext4 `/dev/sda1`, UUID `14ef1cfa-28ee-4986-8989-2e248896bf07`.

The static PV is rooted at `/mnt/hdd/shared-dev/profiles/mongo-pilot-single/mongo/`. Its single member mounts `default/member-0`; the ordinal layout is retained for possible future member additions. Local PV/PVC are explicitly bound, Retain, ReadWriteOnce, 50Gi advertised capacity, node affinity and no dynamic provisioner. Protected identity and inventory live beside the Mongo root in `identity/`.

There are no CPU/memory requests or limits on Mongo or the chart proxy. WiredTiger cache is explicitly 0.5 GiB; configured oplog size is 256 MiB. These are not hard total-memory/disk caps. An idle post-check sample was 4m CPU / 82 MiB for the active Mongo member; this is not a capacity guarantee.

## Verification passed

- One PRIMARY in replica set rs0; no secondaries, horizons or TLS configuration.
- Plain direct-IP authenticated reads/writes from the PC-side client without hostname overrides or CA mounts.
- Internal cluster-DNS replica-set discovery and access to the same fixture.
- Majority transaction and change stream succeed on the single-member set.
- Unauthenticated reads and admin operations by the application user fail.
- Data, credentials and PVC/PV identities differ across two profiles; profile A’s credentials cannot authenticate to B, and cross-namespace TCP is denied.
- LAN destination and LAN-source-to-Tailscale access denied in host-side tests; no NodePort bypass introduced.
- Full data-only teardown and recreation preserve the exact original fixture, member marker and PV/PVC UIDs.
- Startup marker failures and wrong/missing HDD identity fail closed in isolated tests; no live disk unmount/corruption used.
- Existing Go workload specifications and cluster CoreDNS match the baseline. Existing `shared-dev-access` configuration hash remains `621b4324c5e27a29ed5131bd38cf37a6c89789b261304e1da57b0716c956d1ca`.

On 2026-10-01, Sean reported successful connection from Mac Compass and reading `token: "single-member-fixture-v1"` in the retained fixture. This is user-reported client verification; Mac writes and the Compass version were not reported. There is deliberately no member-failover proof for a one-member set. Developer account/RBAC provisioning and full frontend-profile isolation are later work.

## Retirement and evidence

After explicit authorization, the old `/mnt/hdd/shared-dev/profiles/mongo-pilot` directory, including its obsolete three-member identity/private CA, was deleted only after all its Pods/controllers stopped and its exact retained volume UIDs were checked. Its old PV/PVC/storage class and namespace were removed. No force reconfiguration was performed. Backups and other profiles were not deleted.

The `shared-dev-dns-pilot` namespace was removed after shutdown; no Tailscale DNS listener remains on port 53. Mac `/etc/resolver/pilot.dev.test` removal requires the local command in the walkthrough; the agent has not run that on the Mac. API DNS is undecided and separate.

Canonical code: `/home/sean/workspace/chart-infra`. Private generated state: `~/.local/state/chart-infra`:

- `profiles/<profile>/manifest.review.json`: Mongo manifest with the keyfile Secret redacted. Regenerate through the runner; do not apply this review artifact.
- `access-releases/<hash>/haproxy.cfg`, `access-routes.json`, `access.json`: exact plain TCP routing configuration.
- `profiles/<profile>/verification.json`, `retention.json`, `storage-guards.json`: checks and retention evidence.
- `profiles/mongo-pilot-single/macos-compass-user-reported.json`: Sean’s reported Mac connection/read result, separate from automated PC-side checks.
- `profile-isolation.json`: two-profile evidence.
- `retirement-20261001.json`: exact authorized retirement record.
- `profiles/<profile>/compass-uri.txt`, `internal-uri.txt`: private connection exports.

Historical evidence remains in the old state folders and `retired/`; it describes a retired deployment. New commands neither fetch source nor start live collectors. No production was accessed.

## Sean's frontend-backend pilot — 2026-10-01

The subsequent milestone deployed one complete offline application profile in `chart-sean`: auth, tharamine, Orange, a private disposable Dragonfly cache and Sean's single-member Mongo. Other developers' full application profiles have not been deployed or verified. The earlier two-profile result covers Mongo data/auth/network isolation only.

- API: **http://100.66.127.115:13000**, chart-only TCP route to Orange port 3000. Tailscale bind and source ACL; no Ingress/Traefik or public/LAN listener added.
- Mongo: **100.66.127.115:27018**, same Mongo 7.0.43 pin above; `rs0`, `directConnection=true` for laptops. Per-service users have read/write access only to their own database: auth `auth`, tharamine `orange`, Orange `orangeV2`.
- Mongo data: `/mnt/hdd/shared-dev/profiles/sean/mongo/default/member-0`. Persistent generated keys/config/inventory: adjacent `identity/apps/`, outside Mongo's data PV, protected by host file permissions.
- No application Kubernetes Secrets were created or modified. App configs/keys are read-only static mounts from the protected HDD identity directory. The Mongo keyfile Secret follows the existing authorized Mongo lifecycle.
- App source/dependency mounts are read-only in runtime Pods, with writable `/tmp`; host TypeScript edits are watched by the explicitly resolved `tsx watch`. No `.env.local`, production dotenv files or dotenvx keys are loaded by these backend processes. The laptop frontend uses the development settings in the human guide.
- Static Retain PV/PVC pairs hold source, dependencies and config. Mongo's capacity declaration is 50Gi; app mount declarations are 10Gi each. These describe directory-backed volumes, not disk reservations/quotas. No CPU/memory requests, limits or namespace quota were introduced.
- Dragonfly is an authenticated, private app cache, deliberately separate from the existing Go profile Dragonfly. Cache contents are disposable; database persistence is Mongo's responsibility.
- NetworkPolicy permits same-profile app traffic, Mongo and cluster DNS. External/other-profile traffic is denied. SDK telemetry and background workers are disabled; SES uses closed loopback, dummy credentials, disabled instance metadata and one attempt. Ordinary live market/metadata/history integrations are not enabled.
- Laptop frontend uses the supplied offline Vite wrapper: only application API proxies survive, external browser fetch/script/frame connections are blocked by CSP, and known market-data proxy paths return 503. Blank charts/external-feature failures are expected. No live chart feed was exercised.

### Exact application versions

| Component | Tested revision/version |
| --- | --- |
| auth-service-backend | `f680833f49da7123d04d122acc87b3b61ff20d15` |
| tharamine-user-service | `a9b4cad33fb038a0585db474211e1982bdbe28e8` + reviewed offline-worker/HDD wait-timeout patch |
| orange-v2-backend | `1033e2cba3d01a381ad4d8c634713325c66b837b` |
| script-migration | `6260b7917482116e6e1976491c0b883f13c31ba2` + guarded synthetic role fixture |
| kiyotaka-frontend | `9939fee27831cfd3137dc21fd6ecce2bc42872e6` + copied offline Vite wrapper |
| Node / pnpm | `24.20.0` / `11.28.2` |
| Toolchain base image | `node:24.20.0-bookworm-slim@sha256:6642ef280aebc09c4541bee0b15c9f89f0f3f3c247ddee79ae1d37eddfdcbbaa` |
| Deployed toolchain image | `localhost:5000/chart-infra/node@sha256:6bece393b989747b6787a66e1e75143a62fac289b41fa7abdd4ca6f9b76eb369` |
| Dragonfly app cache | `docker.dragonflydb.io/dragonflydb/dragonfly@sha256:748447aa24ee7d14e28bb9bc2869dc62c1e14396bff6df8c83e8e9a6643eb65d` |
| Browser acceptance driver | Playwright `1.63.0`, headless Chromium `153.0.8010.12`, SwiftShader |

The exact toolchain Debian package inventory is in `node-runtime.json`. Application transitive versions are pinned by each repository's frozen pnpm lockfile; dependency-input hashes and image identity are recorded per install. The private npm configuration was verified without recording its tokens. Multiple entries for the same registry authentication key use the final entry; they are not a fallback token list. Npm credentials are installer-only read-only mounts and are absent from the toolchain image and runtime Pods.

### Verified results and limits

- All five frozen dependency installs succeeded, including required private packages. Native dependencies were installed inside this Intel/Linux runtime.
- Auth and tharamine health report database up; Orange health reports both upstreams healthy. Tailscale API returns 200; host-side LAN-destination and LAN-source checks are denied.
- Auth's Mongo user cannot read tharamine's database. App TCP access to the other Mongo profile and a reserved documentation external address is denied. No live provider API was used to test egress.
- Browser email-code login and account creation work with `.test` accounts and locally logged codes. The configured SES transport fails at closed loopback; no mail is delivered. Refresh works through the same-origin Vite proxy with Secure/HttpOnly cookies.
- Real browser UI renamed a workspace to **Shared dev offline fixture** and observed its successful save. After runtime recreation a fresh browser context, without saved local storage, refreshed the session and loaded that workspace from the API. The private published-layout fixture was created/read through normal APIs; peer reads return 404 and peer workspace edits return 403. Workspace links themselves are readable by application design.
- Full app and Mongo `down --data-only` followed by `up` retained the exact workspace record, identity hashes and all registered PV/PVC UIDs. A separate ordinary app stop/start also passed browser recovery.
- All three source watchers executed a temporary unique marker after a real source edit, without changing Pod UID or image. Original source was restored and health recovered.
- Tharamine's source-only TypeScript build check passed. The full test-inclusive typecheck reports 745 test-file diagnostics plus a read-only build-info output error in that invocation; it is not a clean full test-suite result. Offline-worker configuration guard checks passed for development (disabled) and staging/production (flag cannot disable workers), in network-disabled config-only tests.
- Existing storage marker/wrong-HDD guard tests passed. No destructive disk test was used.
- Offline Vite wrapper: application health 200, market proxy paths 503, CSP present; browser login and retained-session/workspace recovery pass. The acceptance browser additionally blocked non-loopback networking. These are PC-side Chromium results, not a claim that Sean's laptop frontend has been tested. Earlier Mac Compass success remains separately user-reported.
- Temporary browser Pod, loopback port-forward and completed fixture Jobs were removed after verification; their source/dependency volumes and evidence were retained. Sean's application stack remains running for laptop testing.

This is an offline application pilot, not a completed multi-developer rollout. Pending: second full app-profile acceptance, per-developer Linux/RBAC/helper onboarding, Syncthing pairing, named-dataset switching, source/dependency/runtime-generation activation and market-data chart integration. The current app inventory pins one generation and rejects changed dependency manifests. Existing Go pipeline services, Traefik and Syncthing were not reconfigured.

### Commands and evidence

See [daily use](GETTING-STARTED.md) and [explicit operator preparation](PREPARATION.md). The reusable entry points are `chart mongo`, `chart sources`, `chart runtime`, `chart deps`, `chart apps`; `chart fixtures` remains Sean-pilot-specific. Deploy/start does not fetch code, install dependencies or run migrations.

Private evidence under `~/.local/state/chart-infra`:

- `node-runtime.json`, `sources/sean/pilot.json`, `dependencies/sean/pilot/*.json`: build/source/dependency identity.
- `profiles/sean/apps-manifest.json`: applied app manifests, containing paths but no credential values.
- `profiles/sean/apps-connectivity.json`, `source-checks.json`, `watchers.json`, `user-isolation.json`: service/network/source/user checks.
- `profiles/sean/apps-retention-before.json`, `apps-retention.json`: runtime recreation comparison.
- `chart-fixture-dry.log`, `chart-fixture-live.log`: targeted migration evidence; no migration fleet run.
- `browser/offline-wrapper.json`, `browser/retention.json`, screenshots and private driver logs: browser/proxy results. `browser/session-private.json` and peer token state contain credentials and must stay private.
- `profiles/sean/compass-{auth,tharamine,orange}-uri.txt`: private service-specific Compass connections.
- `profiles/sean/completion.json`: final workload/resource inventory and project file hashes.

## Sean's Mac Mongo import — 2026-10-01

Imported `mongo-rs0-20261001T084046Z/mongo-rs0.archive.gz` from Sean's local Mac backup into `chart-sean`, reachable at `100.66.127.115:27018`.

| Source database | Shared database | Imported documents |
| --- | --- | --- |
| auth | auth | 127 |
| orangeDB | orange | 624 |
| orangeV2 | orangeV2 | 0 (collections/indexes restored) |

The archive describes MongoDB 7.0.37 / Database Tools 100.17.0. Restore used the existing MongoDB 7.0.43 / Database Tools 100.18.0. Archive SHA-256: `2747925f8cb217011302d88afaf9c876fdd655c32ec3b022279a498934678bbd`.

The 232 application collections were first restored under separate `import_20261001_084046_*` staging database names and checked for record-ID, unique-index and index-definition conflicts. No such conflicts were found. Two synthetic role names overlapped logically with the dump; their entire original collection was retained as `auth.roles_pre_import_20261001_084046`, and imported role definitions now occupy `auth.roles`. Other existing data was merged without replacement or collection drops.

Sean's apps and external Mongo route were stopped during the backup/import. A full pre-import gzip archive was saved and validated on the HDD. TTL expiration was temporarily paused on Sean's Mongo during verification, then restored to its original enabled setting. All 751 imported records matched the staging documents before TTL resumed; all 37 pre-existing records were verified retained. The restore reported zero failed documents, and all source index names were verified present. Auth/admin identities, replica-set config and PV/PVC UIDs are unchanged. System databases and the source oplog were not applied; this was an application-data import, not a replica-set point-in-time recovery.

Apps and Tailscale routes were restored. Orange's health endpoint reports both auth and user databases up; all three existing service credentials can access their respective databases. The PC retains its development signing keys; this does not migrate the Mac's signing keys or guarantee portable existing browser sessions. No live integrations or background workers were enabled. This import was verified at the database/API-health level, not through a new laptop browser test.

Source archive, backup, comparison, metadata and restore logs are retained in the private HDD directory:

`/mnt/hdd/shared-dev/imports/sean/mongo-rs0-20261001T084046Z/`

- `before-import.archive.gz`: pre-import full backup, including staging databases; contains sensitive data/auth material and must stay private.
- `restore.log`, `verification.json`, `apply-state.json`: completion evidence.
- `source-sha256.txt`, `archive-metadata.json`, `comparison.json`, `index-conflicts.json`: inspection evidence.

The staging databases and renamed role collection remain retained; no cleanup deleted user data. Temporary Pod-side restore credentials/archive copies were removed. The one-off guarded scripts are in `~/.local/state/chart-infra/`; their checkpoint guards deliberately prevent blindly replaying this completed import. This is not a new generic dataset-switching command.

## Laptop confirmation after import — 2026-10-01

Sean reported: “it works, workspaces are being saved” after connecting the laptop frontend to the shared backend. Record this as user-reported application connectivity and workspace-save success. Browser version, fresh login method and laptop reload/restart persistence were not separately reported; earlier automated retention evidence remains distinct. Evidence: `profiles/sean/laptop-workspace-user-reported.json` in private chart-infra state.

## HDD backend sources and Sean Syncthing preparation — 2026-10-01

Sean requested HDD placement for backend source and Linux dependencies, and an agent-first onboarding document with explicit paths rather than a fixed workspace convention. Current scope remains Sean; no second full developer profile was provisioned.

The three active backend checkouts now run from `/mnt/hdd/shared-dev/profiles/sean/source/workspaces/pilot/<repo>`. Their Linux `node_modules`/pnpm stores now live under `/mnt/hdd/shared-dev/profiles/sean/dependencies/<repo>/<input-hash>/node_modules`. Source copies were verified including Git metadata/dirty edits; dependency copies were verified by contents and symlink targets. The three dependency trees total approximately 1.2 GiB. Old SSD source/dependency copies and their PV/PVCs remain retained as rollback material; no disk space was reclaimed by deleting them. The frontend acceptance tooling and script-migration caches were not part of this three-backend migration.

`chart sources relocate` and `chart deps relocate` prepare verified copies without changing the running app selection. `chart apps select --workspace NAME` requires stopped apps, verifies dependency inputs/runtime and offline worker guards, preserves the prior selection and uses new volume references instead of rebinding immutable PVs. All previous volume UIDs remain guarded. Prepared dependency generations are immutable/reused; installs never modify a mounted ready cache. Synced source requires a matching laptop fingerprint and paused owned folders before selection.

HDD activation verification passed: app/database health, all retained key/config hashes, original volume identities and saved workspace contents. All three `tsx` watchers executed a controlled source marker from the HDD with unchanged Pod UID/image; original files were restored. A route re-add encountered a transient port-bind conflict after withdrawal; the access preflight now uses address-reuse semantics consistent with HAProxy, allowing closed connections to finish without blocking route recreation. The API is back at its original port 13000.

Dedicated Syncthing deployment:

- Namespace/controller: `chart-sean/syncthing`; one non-root Pod, restricted container, no service-account token or CPU/memory requests/limits.
- Image: `syncthing/syncthing:2.1.5@sha256:397aa00b92b48d65540ea3ae3cbf271b87bdccbe07a0b7bd7d2debc3a7b29138`, matching the inspected existing image version, independently generated state/keys.
- Plain host TCP forwarding at **100.66.127.115:22001** → profile ClusterIP:22000; only Tailscale bind/source addresses. Syncthing's own device-authenticated encrypted transport operates over that route. Port 22000 still belongs to OpenScape.
- GUI/API: authenticated and cluster-internal on 8384; helpers use temporary loopback forwarding and local API-key access. No external GUI port.
- Small retained SSD state: `~/.local/share/chart-infra/sync/sean/state`, static Retain PV/PVC `chart-sean-sync-state` / `sync-state`. Device identity fingerprints and volume UIDs are checked, not regenerated on startup.
- Three HDD mirrors: `/mnt/hdd/shared-dev/profiles/sean/source/mirrors/laptop/{auth-service-backend,tharamine-user-service,orange-v2-backend}`. Each has its own retained source PV/PVC, writable to Syncthing and read-only when selected by applications.
- Folders `chart-sean-laptop-{auth,tharamine,orange}` are **receive-only, paused and not yet paired to the Mac**. The active apps still use the verified `pilot` HDD checkouts. Creating the sync runtime did not activate empty mirrors.
- New-instance discovery, local discovery, relays, NAT traversal, usage/crash reporting and automatic upgrades are disabled. A dedicated deny NetworkPolicy isolates it; the earlier namespace-wide Mongo/DNS allow excludes `chart-runtime=sync` because policies are additive. Existing app Mongo/DNS allowances remain in effect.
- Exclusions are installed on the PC mirrors before pairing and are case-insensitive, including private env/config/key files, Git internals, dependencies and build outputs. Non-secret root env templates are explicitly retained. The laptop helper refuses pre-existing differing ignore rules and overlapping roots rather than overwriting them.

Verified: seven offline safety tests (overlap, symlink/conflict rejection, secret/build exclusions, interrupted and repeated additive pairing), compilation of the helpers, live API/exclusion configuration, denied sync-Pod access to Mongo and a reserved external documentation address, Tailscale listener availability, unchanged existing OpenScape deployment/config fingerprints and healthy application API. Dedicated sync `down --data-only` / `up` preserved identity, folders and PV/PVC UIDs. This retention test used unpaired paused folders; it does not yet prove a real Mac connection survives teardown.

The primary handoff is **docs/AGENT-ONBOARDING.md**, also published in Sean's Obsidian Shared Dev folder. `laptop-sync.py` and `sync_common.py` are optional standard-library command building blocks for an agent: inspect (`plan`), pair while paused, resume/pause, status and fresh-scan/fingerprint verification (`wait`). Repository paths are explicit and retained per profile. Setup never changes unrelated laptop Syncthing defaults/folders or the existing PC OpenScape configuration. The laptop initiates SSH; reverse SSH access is unnecessary.

Still pending on the actual Mac: inspect/select the three checkout paths and existing local Syncthing config, reconcile the offline worker patch in the authoritative laptop source, pair/resume, verify real source transfer, explicitly prepare Linux dependencies/select the mirror, and prove hot reload/reconnect/exclusions on that path. Do not label the offline tests as a completed Mac pairing. Separate account/RBAC onboarding and named Mongo datasets remain pending. No live integrations were enabled.

Private evidence:

- `source-relocations/sean-pilot-*.json`, `dependency-relocations/sean-pilot-*.json`: verified copy receipts.
- `profiles/sean/hdd-selection-before.json`, `hdd-selection.json`, `watchers.json`: active HDD selection/data/key/hot-reload checks.
- `profiles/sean/sync-openscape-before.json`, `sync-check.json`, `sync-retention.json`: unrelated-config/network/retention checks.
- `profiles/sean/sync-manifest.json`: owned runtime/storage/network manifests.
- Syncthing inventory and identity fingerprints: `~/.local/share/chart-infra/sync/sean/inventory.json`; its API/GUI credentials stay private outside sync roots.

## Mutagen source transport — 2026-10-01

Sean approved replacing dedicated per-profile source Syncthing with Mutagen over SSH. This section supersedes the earlier Syncthing onboarding/runtime instructions. The existing OpenScape deployment/config is unchanged.

Installed PC interfaces: `chart sync prepare|export|status|register|checkpoint|invalidate`. `prepare` is an operator-only explicit registration step; `register/checkpoint/invalidate` are invoked over SSH by the laptop helper. `laptop-sync.py` now implements `plan|setup|resume|pause|status|wait|freeze` using exact owned Mutagen session IDs. This replaces its former Syncthing pairing interface. `sync_common.py` owns ignore/fingerprint policy `chart-mutagen-v1`; old helpers/runbook are retained under `retired/2026-10-01-syncthing-source/`. `syncthing_legacy.py` exists only for guarded retirement/rollback and refuses startup after Mutagen registration.

**Pinned executable:** Mutagen 0.18.1, official GitHub release. Linux amd64 archive SHA-256 `7735286c778cc438418209f24d03a64f3a0151c8065ef0fe079cfaf093af6f8f`, verified against the release SHA256SUMS. The PC verification client and agent bundle are at `~/.local/share/chart-infra/tools/mutagen/0.18.1/`. Mac arm64 archive SHA-256 `6f810416d9e5fc4fd5e18431146f8b3c5a2056ba5a24f76c1e66da86eb3257e2`; Mac amd64 `7d06f7d8fcfe90bc7e55cc834a2f2f20c2e0af9ea9bc35911fc4341ad56a9bbf`. Mac installation has not been performed from this PC. Application images, Node/pnpm and database versions are unchanged.

Sean's existing empty HDD mirrors and `sync-{auth,tharamine,orange}-source` claims were adopted explicitly. No source tree was erased/recreated. Registration, UID/GID, per-root marker, policy hash, workspace and retained volume UIDs are in `/mnt/hdd/shared-dev/profiles/sean/identity/source-sync.json`. New source files are written as Sean's SSH UID 1000; app Pods already run as UID 1000 and mount them read-only. Remote transfer staging uses `neighboring`, on the HDD beside each mirror. Small Mutagen client/agent metadata remains in user home storage. No new persistent network listener or sync Deployment is required.

The owned `chart-sean/syncthing` Deployment, Service and `chart-sync-isolation` NetworkPolicy were removed; its Tailscale route `sync-sean`/port 22001 was withdrawn. All original PV/PVC UIDs, source directories, state/config and device keys remain retained. The existing namespace policy exclusion for `chart-runtime=sync` is retained; there is no broadening of app egress. No unrelated resource or Syncthing configuration was changed. Removing the route briefly recreated the chart proxy; the existing API/Mongo routes were retained and API health passed afterward.

The apps remain on workspace **pilot**, using the already verified HDD checkouts/dependencies. All app and Mongo Pod UIDs, selected sources, protected identity/config hashes and OpenScape deployment/config fingerprints matched the pre-migration snapshot. Mongo/imported workspaces were not modified by this transport migration. No actual laptop session has been registered yet, and no empty mirror was activated.

Policy/activation details:

- `one-way-safe`, full scans, SHA-256 hashing, case-insensitive ASCII exclusions generated from one policy, no inherited global Mutagen configuration, owner-only file/directory defaults. Known root `CLAUDE.md -> AGENTS.md` is excluded; other included symlinks fail source validation. Mutagen itself does not transfer symlinks.
- All source/private env/key/npm/netrc/dependency exclusions and root non-secret template exceptions apply before transfer. Existing `.stignore`/`.stfolder` and new identity markers remain outside fingerprints/sync.
- `wait` flushes the intended sessions, checks connectivity/conflicts/problems, compares laptop and PC fingerprints and records HEAD/dirty metadata. Extra PC-only files are rejected by fingerprint checks even when one-way-safe reports no conflict.
- `freeze` flushes then pauses all owned sessions and writes a matching paused checkpoint. The PC records a laptop pause attestation, independently verifies fingerprints/registered volume identities, and never claims it independently queries the laptop daemon. `resume` invalidates the activation attestation before restarting transfer. Developers must use the helper for this contract.
- Source selection and dependency installation require that frozen checkpoint. Profile app/Mongo lifecycle locks serialize PC preparation/selection/checkpoint changes. No source fetch, install, deployment or data reset occurs implicitly.

Verification passed:

- Ten offline safety tests: root overlap, private/mixed-case exclusions, known/unexpected symlinks, case collisions/conflict files, edit/delete fingerprints, extra PC files, wrong-owner/unpaused selection rejection, accepted checkpoint persistence and subsequent mutation rejection, connection/error checks.
- Real **PC-local SSH fixture** using isolated test keys/loopback sshd and a separate Mutagen daemon: automatic SSH agent installation, interrupted/repeated setup preserving session IDs, all three repo transfers, ignores/templates/mount markers, full fingerprints, atomic saves and create/rename/delete, daemon stop/reconnect, remote conflict preservation, extra-file mismatch rejection and frozen checkpoint.
- Three container watchers used the pinned application runtime and actual prepared Linux dependency volumes, with HDD fixture-source PVCs mounted read-only. All executed changed markers after Mutagen atomic writes with unchanged Pod UID/image. These were synthetic watcher processes, not an actual Mac or a switch of the real backend apps.
- Temporary fixture Pod/policy, isolated SSH server and fixture/probe daemons were stopped/removed. Synthetic fixture files/source PV/PVCs and private session evidence remain retained. No CPU/memory requests/limits were introduced.
- Migration verification: API healthy; old listener closed; former sync volumes/device identities intact; app/Mongo Pod identities and source selection intact; OpenScape unchanged. Repeating PC prepare reused the same registration.

Evidence: private `profiles/sean/mutagen-fixture.json`, `mutagen-migration-before.json`, `mutagen-migration.json`, `syncthing-before-mutagen.json` under `~/.local/state/chart-infra`; new successful checkpoints will use `sync-checkpoint.json` and `sources/sean/laptop.json`. Private fixture source roots are under `/mnt/hdd/shared-dev/verification/`; the latest root is recorded in the fixture evidence. The authoritative client handoff is `docs/AGENT-ONBOARDING.md`, published in Obsidian.

Remaining actual Mac work: install verified client, copy the updated helpers, discover/check real repo roots, setup/resume, reconcile the offline tharamine patch on the laptop, freeze, prepare Linux dependencies, explicitly select workspace laptop, then verify real backend hot reload/reconnect and frontend workspace persistence. Only Sean's full app profile is deployed; separate-account/RBAC onboarding and named-dataset switching remain unfinished. Do not claim this PC fixture proves real Mac acceptance.

## Non-interactive SSH kubeconfig correction and local Git — 2026-10-01

The laptop agent reported that `sync.py export/status` failed over non-interactive SSH because k3s' kubectl fell back to `/etc/rancher/k3s/k3s.yaml`. Reproduced with `KUBECONFIG` absent. `common.py` now supplies the invoking user's `~/.kube/config` only when the variable is unset, and every inherited kubectl subprocess sees that value. Explicit overrides, including multi-file values, are preserved. Sean's user config and the k3s server config remain mode 0600; no credentials or SSH/shell configuration were changed.

Read-only `sync status/export` retain registered-cluster, HDD, retained-volume and owner checks but no longer acquire app/Mongo/sync lifecycle locks. They call `mongo.configure(profile)` only to set the profile context; `apps.setup` was just a wrapper for that operation. Reads also skip the mutation-only SSD reserve check so low space does not hide status. Inventory JSON replacement is atomic; a concurrent preparation/storage change may fail a read's identity check and requires a retry, never repair by that read. Mutating actions retain all three locks and the capacity guard.

Verification: five new bootstrap/guard/lock regression tests and the ten source-sync safety tests passed. A real isolated non-interactive SSH session to Sean started with KUBECONFIG absent; both direct `python3 sync.py` and `./chart sync` status/export returned valid JSON while all three lifecycle locks were held. The entire real Mutagen SSH/container watcher fixture then passed when launched through that same non-interactive SSH transport, without an environment prefix, login shell, PTY or dotfile sourcing. Test servers/daemons and temporary Pod/policy were cleaned up; fixture data/volumes remain retained. This is PC loopback SSH evidence; the actual Mac should retry its two commands and continue onboarding.

Evidence: `profiles/sean/noninteractive-ssh.json`, `noninteractive-mutagen-fixture.log` and the latest `mutagen-fixture.json` in private chart state. No app source selection or Mongo data changed.

Initialized a standalone local Git repository on `main`, with sensitive/generated path ignores. Reviewed source/docs as text and scanned common credential signatures; no findings. The initial baseline is staged for review, with no commit, remote or push. Runtime identities/config/evidence remain outside the checkout. Source changes can now be reviewed with `git diff --cached` before recording the baseline.

## Source policy v2: local tooling exclusions — 2026-10-01

The laptop agent reported successful plan execution and held setup while requesting exclusions for `graphify-out`, `.graphifyignore` and `.husky`. These are now in the shared SKIP policy at any depth, with case-insensitive ASCII Mutagen patterns and matching manifest behavior. The entire Husky hook/generated subtree is excluded. Policy is `chart-mutagen-v2`, hash `268864b26c3cd422916a90906ae0456e3c2af2f4d381e5a78d05460a2dd95bc8`.

Added explicit `chart sync upgrade-policy`: it accepts only the known prior policy/hash, applies existing cluster/HDD/owner/volume/marker guards and all mutation lifecycle locks, rejects registered/checkpointed or populated mirrors, backs up the prior inventory privately, and updates only policy and policy_hash. Sean's registration was verified unused and migrated; registration ID, owner, paths, markers and all PV/PVC UIDs were unchanged. A repeat was a no-op. No runtime, source selection, source data or identity was regenerated.

Verification: 14 source-policy/checkpoint tests plus five kubeconfig/lock tests passed. The real Mutagen/SSH fixture, launched non-interactively, verified root/nested mixed-case graphify/Husky exclusions together with existing secret exclusions and transfer/watcher checks. Non-interactive SSH export returned policy/hash matching the updated PC helper. Actual Mac setup remains pending; re-copy both helper files, rerun plan, then setup. Evidence: private `profiles/sean/policy-v2-verification.json`, `policy-upgrades/before-*.json`, and the latest Mutagen/noninteractive fixture records.

Also narrowed the Git kubeconfig filename ignore so `tests/kubeconfig.py` is included in the staged baseline while actual kubeconfig paths remain excluded. No commit or push was made.

## Unmodified laptop source activated — 2026-10-01

Sean approved stock backend source with no local Tharamine patch. The Mac froze all three clean checkouts, and the PC independently verified that checkpoint. `apps select --profile sean --workspace laptop` accepted it. The three real backends now run from the HDD laptop mirrors. Mutagen remains **paused** for laptop continuation.

| Repository | Exact revision | Dirty |
|---|---|---|
| auth-service-backend | `4c1a55330d28b373d75c1e7771b19e322f55f953` | no |
| tharamine-user-service | `6ca935fad4e88af14448588c0b725adda25fcb9d` | no |
| orange-v2-backend | `51320060017314b1bcae3695556cb95a513b46e4` | no |

Removed the offline-worker source guard from selection/startup and stopped applying the Tharamine patch during source preparation. The patch is historical under `retired/2026-10-01-offline-worker-patch/`. The synthetic script-migration fixture patch remains separate. No mirrored application source was edited.

The synced workspace selects a separate retained Tharamine config generation/PV, with `LOCAL_BACKGROUND_WORKERS_DISABLED` and `MONGO_WAIT_QUEUE_TIMEOUT_MS` absent. Original pilot config, keys, sources, dependencies and PV/PVCs remain intact; selecting `pilot` while stopped restores its original configuration. Other supported development integration-disable flags remain unchanged. Stock worker entrypoints run; this does not enable live capture or delivery.

**Startup result:** stopped apps, restarted Sean's retained single-member Mongo on the HDD, then started auth → Tharamine → Orange. Stock Tharamine's hardcoded `waitQueueTimeoutMS: 10000` succeeded on the first process start with zero container restarts and no wait-queue timeout observed. Five worker-start messages were recorded: attachment scan, attachment GC, Mongo event-subscription delivery, room-alert sweep and workspace expiry logging. Telegram explicitly skipped webhook registration. This tests a fresh Mongo process/WiredTiger cache against retained, already indexed data. The host page cache was not flushed; it is not a fresh empty database or worst-case cold-disk guarantee.

**Egress result:** checked the selected config: no kScript alert URL, Telegram/webhook delivery config, APNs/FCM or R2 credentials, proxy override, or obsolete patch flags. Policies continue to allow same-profile app/Mongo traffic and cluster DNS, with no external IP allowance. From the real running Tharamine Pod, profile Mongo TCP succeeded and a controlled cross-namespace listener was rejected (`ECONNREFUSED`), while the host reached that listener before and after. No live API was contacted. The first test expected a silent timeout; k3s actively rejects, so the assertion was corrected to accept either enforced denial behavior and the test passed. This checks the current unprivileged app network boundary; it is not a guarantee against later policy changes or privileged host access. DNS queries can still leave through cluster DNS.

**Retention/health:** retained workspace content hashes, original identity/config/key hashes and prior volume UIDs matched. All three services passed readiness with databases up, no restarts, and no CPU/memory requests or limits. Auth/Tharamine reused dependency caches; Orange installed its frozen lockfile into a new retained HDD generation. No production credentials, migrations or data resets were used.

Runtime remains Node **24.20.0**, pnpm **11.28.2**, image `localhost:5000/chart-infra/node@sha256:6bece393b989747b6787a66e1e75143a62fac289b41fa7abdd4ca6f9b76eb369`. Mongo remains **7.0.43**, with the existing pinned digest. Twenty-one focused regression checks passed. Evidence is in private `profiles/sean/stock-source-before.json`, `stock-source.json`, `stock-egress.json`, `stock-boot.log` and `stock-tharamine.log` under `~/.local/state/chart-infra`.

**Laptop next:** run `chart_sync resume`, then verify `chart_sync wait`. Prove a controlled edit in each actual backend triggers its watcher with unchanged Pod UID/image, restore the edits from the laptop, and check frontend login plus save/reload. Use only synthetic log markers, never credentials. The earlier first-sync success is laptop-reported and independently matched on the PC; actual Mac-to-real-backend hot reload remains pending. No need to repeat dependency installation or selection unless inputs change.

## PC follow-up — auth backend signing key fixed, 2026-10-01

Confirmed the missing `PRIVATE_KEY_PATH_BACKEND` in the retained auth config. Both `pilot` and `laptop` use that same auth config/PVC, so one additive fix covers both. Generated a dedicated development-only RSA-2048 key pair under `/mnt/hdd/shared-dev/profiles/sean/identity/apps/auth/`, owner-only, and set `PRIVATE_KEY_PATH_BACKEND=/run/chart/backend-private.pem`. The existing read-only config volume already mounts this directory; no source edit or new volume was needed. Existing user/service signing keys were not rotated. The previous config was backed up privately beside the service directories.

The reusable operator command is `./chart apps prepare-backend-key --profile sean`, followed by `./chart apps restart --profile sean --service auth` when first adding the setting. New app profiles provision this identity during `apps prepare`. Repeating provisioning reuses and validates the same key; a configured but missing private key fails rather than silently regenerating. Existing foreign paths, symlinks and mismatched public keys are rejected.

Verified: auth became ready with zero container restarts, the startup error disappeared, and the actual `AdminService.createApiToken` signed a synthetic JWT that verified using the matching public key. That probe used an injected repository stub, so no API users/tokens or other database records were created. This verifies local minting, not downstream consumer trust configuration. More precisely, the missing key affects API-token minting; plain API-user creation does not itself sign. Existing identity files (except the intentional auth config addition) were unchanged; Mongo/Tharamine/Orange/Redis Pod UIDs were unchanged. Auth's Pod was deliberately replaced by its config restart, after the laptop's successful hot-reload checks. Two key-provisioning regression tests and the two stock-source tests passed. Private evidence: `profiles/sean/backend-key-before.json`, `backend-key.json`, `backend-key-probe.log`.

The laptop agent reports successful real Mac transfer/exclusion, all three real backend watchers, restoration, repeated setup and SSH-transport recovery. Sessions are now resumed; the PC confirms the latest checkpoint is not frozen. Remaining client checks: **Sean's actual Tailscale disconnect/reconnect** and **frontend sign-in plus workspace save/reload**. No further pairing, source activation, key copying or backend source patch is needed. The ad-hoc kubectl observation requires no change: use `KUBECONFIG=$HOME/.kube/config` for direct SSH kubectl, or the chart commands.

## Reboot guidance documented and Mac acceptance updated — 2026-10-01

Added daily startup instructions to both guides: after reboot/login, reconnect Tailscale, run laptop `chart_sync status`, then `chart_sync wait`. Paused/frozen sessions remain paused until intentionally resumed with the helper. Sean's choice remains on-demand startup; no launchd entry was installed. The agent guide also documents optional registration, shared-daemon coordination, and re-registration when a versioned executable path changes. Checked against the current helper and official Mutagen lifecycle/pinned macOS implementation; no runtime/source/session changes were made for this documentation update.

The latest laptop acceptance table closes the remaining client checks: Sean's real Tailscale disconnect/offline edit/reconnect was followed by successful synchronization, and Sean reported frontend sign-in plus workspace save/reload. Browser verification remains explicitly user-reported. These are Sean's profile results; other-developer account/RBAC provisioning, named dataset switching and a second full app profile remain separate work.

PC read-only follow-up: auth/Orange fingerprints match their latest checkpoints. Tharamine's current mirror has returned to its original activation fingerprint (`79346db7…`), while the latest checkpoint still records the offline edit (`be31d018…`, dirty true). No files were reset or edited here. Run `chart_sync wait` on the laptop after finishing/restoring edits to record a fresh matching checkpoint; freeze separately before a future dependency/source selection. A prior successful acceptance check does not assert that a later live-edit checkpoint is current.
