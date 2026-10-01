---
updated: 2026-10-01
status: Sean HDD runtime verified; Syncthing receiver ready; actual Mac pairing pending
---

# Fable review follow-up — current decisions

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


Sean’s 2026-10-01 verdict supersedes the earlier follow-up. The original [[Per-profile Frontend Backends — Fable Review]] remains unchanged as historical review evidence.

## Settled decisions

- **Mongo:** ONE data-bearing member per profile, initialized as replica set rs0. The three-member requirement is dropped for all profiles. Keep `<dataset>/member-<ordinal>` directories for possible later explicit expansion.
- **Mongo connection:** one plain TCP route per profile in `~/workspace/chart-infra/access.py`, like the existing TimescaleDB/Dragonfly access pattern. Laptop IP:port URI uses `directConnection=true`; in-cluster clients use cluster DNS and `replicaSet=rs0`.
- **Remove Mongo complexity:** no split horizons, TLS, private CA, HAProxy SNI routes, per-member external Services, SRV or developer resolver file. The CoreDNS pilot is retired. API hostname DNS is separate.
- **Storage/auth retained:** static HDD PV/PVC per profile, dataset subdirectories, keyfile auth, distinct profile DB users, protected identity/inventory, UUID/volume/data-marker guards, explicit WiredTiger cache flag and retention across teardown. No CPU/memory requests or limits.
- **Old synthetic pilot:** Sean explicitly authorized deletion of `/mnt/hdd/shared-dev/profiles/mongo-pilot` after it was no longer used. It and its exact unused storage objects were removed after shutdown. No force reconfiguration, no deletion of other profiles/backups.
- **API exposure:** Tailscale-only. Teammates may access profile APIs without sharing SSH credentials; application auth still applies.
- **Developer SSH:** separate Linux accounts, private workspace/helper state; agents stay on laptops. Persistent revocable profile-scoped helper credentials replace the proposed daily token renewal. Existing Go helper credentials are not silently changed.
- **Source sync:** one Syncthing instance per profile, laptop authoritative/send-only and PC receive-only; onboarding pairs additively without clobbering existing Syncthing. SSD for source/dependencies/sync; HDD for Mongo.
- **Lifecycle:** stop/recreate retains data and identity. Optional data-only teardown removes runtime. Named datasets reuse the profile PVC via subdirectories. Dataset deletion remains explicitly targeted. Source fetching, deploy updates and future live capture are separate.
- **Proxmox:** deferred; keep Ubuntu/k3s. Future live capture requires a cluster-enforced deadline; collectors/refresh/history remain disabled by default.

## Current implementation

Canonical standalone project: `~/workspace/chart-infra`. The Go pipeline remains in its existing meta workspace. New generic Mongo profile commands are installed and the single-member pilot is running. Direct IP access, in-cluster discovery, transactions, change streams, full teardown/restore and two-profile data/auth/network isolation passed. The second synthetic profile is stopped, data retained. See [[Shared Dev — Mongo Pilot]].

On 2026-10-01, Sean confirmed that Mac Compass connects and reads the retained `single-member-fixture-v1` token. This is user-reported connection/read verification; Mac writes were not reported. Sean’s frontend services and private package/source preparation are now implemented; named-dataset switching, developer account/RBAC provisioning, source sync and a second complete app profile remain pending. API hostname/DNS/HTTPS remains a separate engineering decision, not a Mongo prerequisite.

## Plan fixes and verification work

| Review items | Disposition |
| --- | --- |
| 1: ingress | Remove the proposed extra HAProxy listener on occupied 443. Use the existing Traefik listener only with Tailscale-only exposure controls proven first. No Traefik/Minecraft change now |
| 2: source mounts/env | Plan direct watcher execution with injected env, and key/config mounts outside the source tree. Use `pnpm exec tsx` or an explicitly resolved executable so `tsx` does not depend on a global PATH entry. Pre-create the excluded dependency mount point and prove nested mounts before rollout |
| 3: Mongo cache | Require explicit per-member WiredTiger cache configuration. Fable's 0.5 GiB value is an initial test setting, not a measured capacity guarantee |
| 4: offline login | Evaluate the proposed loopback SES endpoint, dummy credentials, metadata disable and bounded retry settings against the pinned AWS SDK. Prove browser-supported login and response latency; do not assume an env variable is honoured or that catching an error makes it fast |
| 5: sync readiness | Force laptop scanning, verify completion/errors/divergence, compare matching content manifests, then prove the restarted application serves that source generation. A readiness check alone may still be observing the old process |
| 6–7: pairing/connectivity | Owned-object GET/compare/patch only; preserve unrelated arrays/settings. Unique profile identities/ports; explicit TCP listener; existing OpenScape instance untouched |
| 8: file watching | Measure per-UID inotify demand and validate identity/permissions. The proposed sysctl values are candidates, not an instruction to change host settings now |
| 9: storage guards | One static HDD Mongo PV/PVC per profile with expected UID/binding checks; validate unique dataset/member subdirectory mapping for the single member Pod. The shared static claim is intentional and supersedes the previous three-claim requirement. Check directories/markers before mongod, including automatic restarts; no empty replacements. Stop all members for dataset switches, retain the claim outside runtime cleanup, and confine explicit deletion to one inactive dataset. Do not unmount the HDD or run `chattr` during review cleanup |
| 10: installation | Set HUSKY=0 for mirror installs; verify private registry access, native package builds and installer-only egress. Stock NetworkPolicy does not implement hostname allowlists by itself; choose an actual installer networking mechanism without broadening app egress |
| 11–12: divergence/excludes | Surface receive-only divergence. Any revert is an explicit previewed source-only operation, never automatic cleanup. Install/verify/hash ignore rules on both sides before transfer and protect the node_modules mount point |
| 13–14: consistency | Laptop branches/workspaces are authoritative in sync mode; no PC Git branch commands on mirrors. Distinguish native Syncthing transport from SSH operations; align SSD state vs HDD Mongo placement |

Fable’s logical-database-suffix suggestion is superseded by Sean’s shared-PVC/subdirectory decision. Two teardown depths are implemented for Mongo. A single initial browser origin and API-only certificate handling remain implementation candidates; no Mongo certificates are required. Preserve the requested optional removal of runtime objects while keeping all data; cache cleanup can be a separate explicit operation. Select certificate handling and browser origins based on the API-sharing answer and validate browser trust/cookies. Port allocation and mount/controller details are engineering work, not additional user decisions.

## Next milestone

Implement one frontend-backend profile using the retained single-member Mongo design. The plain-IP Mac Compass connection/read check passed according to Sean; an optional Mac write check and local removal of the retired resolver file have not been reported. Other Phase 0 checks from the review still apply to apps, mounts, package installation, offline login and API ingress. No production or live vendor access is authorized by this design.
