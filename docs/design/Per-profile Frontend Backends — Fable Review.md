---
tags:
  - development
  - infrastructure
  - review
updated: 2026-09-30
status: independent review of the proposal; review only, nothing deployed, paired, configured, moved or deleted
reviewer: Fable 5.1
---

# Per-profile frontend backends — independent review

Reviewed: [[Per-profile Frontend Backends]], [[Per-profile Frontend Backends — Assessment]], [[Per-profile Frontend Backends — Lifecycle]], [[Per-profile Frontend Backends — Source Sync Options]], plus [[Shared Development Environment — Getting Started]] and [[Shared Dev — Quick Start]] for context. The request is in [[Fable — Review Request]].

**Verdict: not ready for a one-profile pilot yet.** The architecture holds up, but five items below would fail or mislead on the first run. Each has a concrete fix and none requires a redesign.

## What was inspected (read-only)

Everything below marked *verified* came from these read-only checks on 2026-09-30. Nothing was changed, no device was paired, no Syncthing API was called.

- Host: `findmnt`/`df`/`fstab` for `/mnt/hdd`; `lsblk`; `/proc/sys/fs/inotify/*`; `ss -ltn`; `tailscale ip`; `docker ps`.
- Cluster (`kubectl get` only): Traefik Service spec and ServiceLB pod ports; LoadBalancer Services; HelmChartConfig; Ingress/IngressRoute; namespace Pod Security labels; `openscape-sync/syncthing` Deployment spec; `dev-sean-fixture` NetworkPolicies; StorageClasses and PVs; k3s version.
- Existing Syncthing: `config.xml` under `/home/sean/services/openscape-sync/state` read with API key and device IDs redacted; state directory sizes.
- Runner: `access.py`, `common.py`, `backing.py`, `profile.py` in the `feat-shared-dev-k3s` k3s runner directory.
- Source snapshots under `meta_repo/zmeta_docs/frontend-backends-evaluation/`: `package.json`, `pnpm-workspace.yaml`, `.gitignore`, `.gitmodules`, auth login-code and email services, auth/tharamine/orange environment and DB-connection files, orange `app.ts` and `redis.ts`.
- Upstream docs: Syncthing REST config, ignoring, folder types and config reference; k3s networking services.

Key verified facts:

| Fact | Value |
| --- | --- |
| Traefik Service | `LoadBalancer`, LAN IP `192.168.100.245`, NodePorts 32323/31730 allocated, no `loadBalancerSourceRanges`, no HelmChartConfig, no Ingress objects |
| ServiceLB pod for Traefik | hostPorts 80 and 443 with **no hostIP**, so all host addresses including Tailscale are captured |
| OpenScape Syncthing | hostNetwork, `runAsUser` 1000, listens on `tcp://100.66.127.115:22000`, discovery/relays/NAT already disabled, folder `fsWatcherDelayS=2` |
| Laptop device in that config | `sean-mac`, pinned `tcp://100.67.218.1:22000` |
| Tailscale ports in use | 5432 5433 6379 8765 15432 16379 19092 **22000** 25432 26379 35432 36379 50435 |
| inotify limits (per uid) | `max_user_watches` 65536, `max_user_instances` 128 |
| `/mnt/hdd` | `/dev/sda1` ext4, UUID `14ef1cfa-…bf07`, fstab `defaults 0 1` (no `nofail`), 1.7 TiB free; `shared-dev/` is 0700 sean |
| Root filesystem | single NVMe partition, 915 GB, 602 GB free; existing SSD storage class stores PVCs under `/var/lib/rancher/k3s/storage` on this same partition |
| StorageClasses | `local-path` (Delete) and `shared-dev-ssd-retain` (Retain); both dynamic `rancher.io/local-path`; no HDD class yet |
| k3s | v1.36.3; StatefulSet PVC retention policy is GA |
| Profile RBAC | get/list/watch, pods/exec+portforward, some create/update/patch, Jobs create/delete; tokens issued with `--duration=8h` |
| Dev scripts | all three: `dotenvx run -f .env.local -- tsx watch src/index.ts`; Orange prefixes `pkill -f ".../redis-memory-server"` |
| Husky | 9.1.7 in all three; `prepare` runs `husky` |
| pnpm `allowBuilds` | memory-server postinstalls blocked; auth allows `bcrypt`, tharamine allows `@sentry/node-native-stacktrace` |
| Submodules | `tasks` and `llm` in all three repos |
| Mongo DB name | all three read `MONGO_DB_NAME` from env; auth default `user-service`, tharamine default `orange`, Orange requires it |
| Auth login-code | logs the code when `NODE_ENV` ∉ {staging, production}; then `await emailService.sendEmail` inside try/catch; SES client built with region only |
| Orange startup | `warmServiceToken()` runs before `app.listen` |
| Source size | three repos without `.git` ≈ 43 MB, ≈ 3,400 files |
| Existing Syncthing state | 4.5 MB total, of which 4.4 MB index for 204 tracked files |

## Prioritized findings

### P1 — would fail or mislead on first run

**1. The 443 exposure design cannot work as written.**
*Verified.* ServiceLB's hostPort DNAT already captures 443 on the Tailscale address. A Tailscale-bound HAProxy frontend on 443 would bind successfully but never receive a packet, because the DNAT happens in PREROUTING before local delivery.
Fix: remove the "Tailscale-bound TCP frontend forwarding 443 to Traefik" from the main note's *Hostname routing through Traefik* section. Use Traefik directly at `100.66.127.115:443`. Restrict exposure with a `HelmChartConfig` for `traefik` in `kube-system`:

```yaml
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata: { name: traefik, namespace: kube-system }
spec:
  valuesContent: |-
    service:
      spec:
        loadBalancerSourceRanges: ["100.64.0.0/10"]
        allocateLoadBalancerNodePorts: false
```

The k3s docs do not state that ServiceLB honours `loadBalancerSourceRanges` (klipper-lb has a `SRC_RANGES` mechanism); verify on a scratch Service before relying on it. Leave the Minecraft LoadBalancer Services alone. Refs: main note *Hostname routing through Traefik*; Assessment §2.

**2. `.env.local` plus dotenvx contradicts the read-only source mount.**
*Verified.* All three `dev` scripts load `.env.local` from the working directory; the file is correctly excluded from sync and the source mount is read-only, so it cannot exist there. A single-file `subPath` mount inside a read-only volume fails at mount-point creation. The same nesting problem applies to the `node_modules` volume.
Fix: never run `pnpm dev`. Run the watcher directly with environment from a Secret:

```yaml
command: ["tsx", "watch", "src/index.ts"]
envFrom: [{ secretRef: { name: <profile>-<service>-env } }]
```

This removes dotenvx and Orange's `pkill` line (it only targets redis-memory-server, moot once `REDIS_URL` is set). For `node_modules`: pre-create an empty `node_modules` directory in each mirror on the host, ignore it in Syncthing **without** the `(?d)` prefix, and prove the nested read-only-parent/writable-child mount in a throwaway Pod before building anything else. Refs: main note *Mounted source and hot reload* steps 4–5; Lifecycle *Changes that need more than a save*.

**3. WiredTiger will size itself from host RAM.**
No container memory limits means each mongod defaults its cache to roughly half the machine. Twelve members across four profiles would try to claim far more than exists.
Fix: explicit flag on every member, then measure.

```text
mongod --replSet rs0 --wiredTigerCacheSizeGB 0.5 ...
```

Refs: main note *Lifecycle and implementation order* (WiredTiger paragraph); Assessment §6.

**4. Offline login will stall, not fail, under default-deny egress.**
*Verified.* The email-code service logs the code in local mode and then awaits the SES send inside try/catch. With egress *dropped* rather than refused, the AWS SDK retries against unreachable endpoints and may probe the instance-metadata address for credentials, so the sign-in request hangs for minutes before the catch runs.
Fix: make the failure instant instead of writing a mail adapter.

```text
AWS_EC2_METADATA_DISABLED=true
AWS_ACCESS_KEY_ID=local
AWS_SECRET_ACCESS_KEY=local
AWS_ENDPOINT_URL_SES=http://127.0.0.1:1
AWS_MAX_ATTEMPTS=1
```

The email-code flow is then a real browser-supported offline sign-in: the developer reads the code from auth logs via the SSH helper. Verify the frontend actually offers email-code sign-in at the selected revision, and verify the SDK version honours `AWS_ENDPOINT_URL_SES`. Ref: Assessment §4.

**5. Sync completion must be proven before a test claims current code.**
Syncthing's completion API can report 100 % before the laptop has noticed a save, because the folder watcher batches changes for `fsWatcherDelayS` (default 10 s). The helper's wait-for-sync must, in order:

1. `POST /rest/db/scan?folder=<id>` on the laptop (forces an immediate scan).
2. Poll laptop `/rest/db/completion?folder=<id>&device=<pc-id>` until `completion == 100` and `needItems == 0`.
3. Compare a content fingerprint computed identically on both sides over the non-excluded file list (laptop: `git ls-files` plus untracked-not-ignored, minus Syncthing exclusions; PC: the mirror).
4. Wait for the backend readiness probe to pass again after the watcher restart.

Record the fingerprint, laptop `HEAD` and dirty flag in every test report. Refs: Source Sync *Requirements whichever tool is selected*; Lifecycle *Preferred proposal* step 4.

### P2 — must be designed in before the pilot

**6. Pairing: the config API replaces, it does not append.**
*Verified from Syncthing REST docs.* `POST /rest/config/folders` and `/rest/config/devices` **replace** an existing entry with the same ID; `PATCH …/{id}` replaces the given child objects; `PUT` replaces the whole config. A repeated folder POST silently drops that folder's device list.
Fix: GET → compare → refuse on collision; on re-runs PATCH only owned objects. The laptop already holds a device entry for the OpenScape instance and the PC's OpenScape config holds `sean-mac`; the profile instance is a **new** device ID, so pairing is purely additive. Never touch the laptop's `/rest/config/options`, `/rest/config/gui` or `/rest/config/defaults`. Check `/rest/config/restart-required` after changes. Ref: Source Sync *First-time setup* steps 3–4.

**7. Port 22000 on the Tailscale IP is taken.**
*Verified.* The OpenScape instance uses host networking and listens on `tcp://100.66.127.115:22000`. Allocate a distinct port per profile using the existing bind probe and the `tcp-request connection reject if !{ src 100.64.0.0/10 }` pattern in `access.py`. In the profile instance set `listenAddresses` explicitly to a single `tcp://0.0.0.0:22000` (the default also enables QUIC and relays) and disable `globalAnnounceEnabled`, `localAnnounceEnabled`, `relaysEnabled`, `natEnabled`, usage reporting and crash reporting. Laptop device entry for the profile instance: pinned `tcp://100.66.127.115:<port>`. PC device entry for the laptop may stay `dynamic`; the PC never dials. Ref: Source Sync *Connectivity*.

**8. inotify limits are per uid and already shared.**
*Verified.* 65536 watches / 128 instances per uid; the OpenScape Syncthing runs as uid 1000, same as Sean's desktop session. Profile Syncthing (watching three repos each) plus `tsx watch` per service will share that pool if they also run as 1000.
Fix: `/etc/sysctl.d/90-shared-dev.conf` with `fs.inotify.max_user_watches=1048576` and `fs.inotify.max_user_instances=1024`; run each profile's Syncthing and app Pods as that developer's own uid so limits and file ownership are per profile. Ref: Source Sync *Per-profile layout*.

**9. HDD guard and empty-volume guard.**
*Verified.* `/mnt/hdd` mounts by UUID with `defaults 0 1` and no `nofail`, so a missing disk halts boot in emergency mode rather than continuing silently — good. But both existing StorageClasses are dynamic local-path provisioners; a missing PVC under a `volumeClaimTemplate` would be quietly satisfied with an empty SSD directory, and mongod happily initialises an empty `/data/db`.
Fixes:
- Keep the unmounted mount point empty and immutable: `chattr +i /mnt/hdd` while unmounted (operator step).
- `shared-dev-hdd-retain` StorageClass with `provisioner: kubernetes.io/no-provisioner`, `volumeBindingMode: WaitForFirstConsumer`, `reclaimPolicy: Retain`.
- Pre-create PVCs with `volumeName` pinned; do not use `volumeClaimTemplates`.
- Init container that refuses to start mongod unless `/data/db/.dataset-id` matches the inventory.
- StatefulSet `persistentVolumeClaimRetentionPolicy: { whenDeleted: Retain, whenScaled: Retain }`.
- Chown member directories to the pinned Mongo image uid (999); do not rely on `fsGroup` for local PVs.
Ref: Lifecycle *Put Mongo data on the HDD*.

**10. Dependency install needs egress and possibly a toolchain.**
*Verified.* Husky 9.1.7 exits 0 with ".git can't be found", so the `.git` exclusion is safe; still set `HUSKY=0` in the install Job. `allowBuilds` already blocks memory-server postinstalls, but auth builds `bcrypt` and tharamine builds a Sentry native module, which fetch prebuilt binaries (node-pre-gyp, GitHub releases) or compile. The install Job needs its own NetworkPolicy allowance for the registry and GitHub, or build tools in the dev image; application Pods must not inherit that allowance. Private `@orangecharts` registry auth remains unverified. Ref: Assessment §3.

### P3 — correctness details, contradictions, stale text

**11. Receive-only divergence is silent until the next laptop edit.**
*Verified from folder-type docs.* A locally modified mirror file stays modified until the laptop changes that same file, then the cluster version wins. Add a `sync revert` command wrapping `POST /rest/db/revert?folder=<id>` as the sanctioned reset, and surface `receiveOnlyChangedFiles` from `/rest/db/status` in profile status. Ref: Source Sync *Syncthing can also work*.

**12. Ignore rules.**
*Verified from ignoring docs.* Patterns without a leading `/` match at any depth, so `.git` also covers the `tasks` and `llm` submodule `.git` files. `.stignore` itself never syncs; `#include` of a synced file works. Recommend the setup tool writes identical rules on both sides via `/rest/db/ignores` and stores a hash of them in the inventory. Do not use `(?d)` on `node_modules` (on the PC it is a mount). Minimum rule set:

```text
.git
.env
.env.*
.npmrc
.netrc
*.pem
*.key
node_modules
dist
coverage
.pnpm-store
.DS_Store
```

**13. Contradictions and stale text.**
- Main note *Source and prerequisites* and Lifecycle *Start with clean code* describe PC worktrees and `source prepare --workspace --ref`. In sync mode the laptop branch **is** the workspace. Drop those PC-side commands for sync mode: one mirror per profile per repo; "fresh code" is a laptop git operation; the runner records the fingerprint/revision reported by the laptop helper.
- Lifecycle *Synchronization behavior* says "Transfer source changes over SSH to that directory". That is the Mutagen/rsync text, not Syncthing.
- Main note proposes database `auth`; the code default is `user-service`. Set `MONGO_DB_NAME` explicitly either way.
- Main note anchors S1–S10 are older SHAs than the Assessment's reviewed revisions; recheck when pinning.

**14. Storage placement — decided 2026-09-30: SSD.**
Syncthing *state* (certificate, key, `config.xml`, index database) is separate from the *synced files* (the mirrors). State is proportional to file count: the existing instance holds 4.5 MB for 204 files; a profile tracking ≈ 3,400 source files will be tens of MB. Mirrors are ≈ 43 MB for all three repos. Both are noise against 602 GB free. The root filesystem is a single NVMe partition, not an LVM volume, and the SSD storage class already lives on it, so "SSD" and "root" are the same pool.
Decision: Syncthing state and mirrors stay on the SSD (scan latency matters, size is trivial). Mongo datasets stay on the HDD (the only unbounded writer). The SSD consumers this plan actually adds are `node_modules` volumes and the pnpm store (hundreds of MB to a few GB per service per profile); extend the runner's existing 25 % free-space reserve guard to `prepare`. Supersedes the Source Sync suggestion of an HDD Syncthing state root.

### Complexity to cut

- **Datasets as separate StatefulSets + PVC triples.** All three apps take `MONGO_DB_NAME` from env. A dataset can be a database-name suffix on one replica set per profile (`user-service__empty-login-test`, `orange__empty-login-test`, `orangeV2__empty-login-test`): switching is three env vars and a restart; deletion is an explicit `dropDatabase` of three named databases; still on HDD, still retained, still explicit; no per-dataset replica-set identity, inventory of volume UIDs or app-URI regeneration. Keep PVC-per-dataset only if per-dataset Mongo version pinning is required.
- **Three teardown depths** (`down`, `down --backing`, `down --retain data`) plus cache removal. Collapse to `down` (apps + route) and `down --all` (apps, Mongo, Syncthing runtime; every volume and identity kept); move cache removal to `prepare --clean`.
- **Operator-managed dev CA.** Each developer mints `api.<profile>.dev.test` with mkcert on their laptop (root installed into their own OS/browser trust stores by mkcert), and the setup tool uploads cert + key to the profile TLS Secret. No root distribution; CA key never leaves the laptop.
- **Distinct UI origin per profile.** Defer until someone runs two profiles concurrently; `https://localhost:5173` suffices for the pilot.
- **Bidirectional probe** in setup step 6 is cheap; keep it.

## Decisions needed from Sean

1. Restrict Traefik to Tailscale via `loadBalancerSourceRanges` (verify first), or accept LAN reachability of a controller that serves only 404 today.
2. Dataset model: database-suffix (recommended) or PVC-per-dataset.
3. Twelve mongods eventually, or three-member sets for Sean only and one-member sets for other profiles until election tests are needed there.
4. Per-developer OS accounts and uids now (affects inotify limits, mirror ownership, SSH).
5. Daily kube access for the SSH helper: `profile.py` issues eight-hour tokens, which will not survive a working day of `logs`/`restart` calls. Options: long-lived namespace-scoped ServiceAccount token per developer, or the helper invokes the runner under the owner via a constrained wrapper.
6. ~~Where mirrors, dependency volumes and Syncthing state live.~~ **Decided: SSD.**
7. Sync port allocation scheme per profile (22000 is taken on the Tailscale IP).

## Minimal phased acceptance plan

1. **Phase 0 — no profile changes.** Throwaway Pod proving read-only source + nested writable `node_modules` mount. Scratch LoadBalancer Service proving ServiceLB source ranges. `pnpm install --frozen-lockfile` in the pinned dev image with `HUSKY=0` and registry auth. Email-code request latency with the AWS settings above against a scratch auth process.
2. **Phase 1 — Sean's Mongo.** Three members on the HDD with the cache flag and dataset marker; exactly one primary and two secondaries; transaction commit and rollback; step-down; `down --all` then `up` returns the same records and PVC UIDs; simulated missing volume fails before any Pod starts.
3. **Phase 2 — apps.** Secrets via `envFrom`, direct `tsx watch`, readiness gated on Orange's service-token warmup; email-code login through the API; layout save and reload; all vendor workers confirmed off at the pinned revisions.
4. **Phase 3 — browser.** Traefik host rule with an mkcert certificate, Vite proxy, real sign-in, refresh, layout save, restart, reload; unknown hostname returns 404; auth/tharamine/Mongo not reachable from the laptop.
5. **Phase 4 — sync (only after explicit go-ahead to pair Sean's laptop).** Diff of laptop `config.xml` and PC OpenScape `config.xml` before and after; second setup run is a no-op; a fake `.env.local` and a fake `*.pem` never arrive; edit/create/rename/delete reach the watcher with unchanged image digest and Pod UID; wait-for-sync fingerprint matches; PC-side modification is detected and reverted; disconnect/reconnect recovers.
6. **Phase 5 — second profile.** Independent keys/Mongo/Redis/sync; peer denial through network and credentials; profile B cannot see or select profile A's storage or folders.

## Blockers for a one-profile pilot

Findings 1–5 plus Phase 0. Everything else can be designed in during Phases 1–4.
