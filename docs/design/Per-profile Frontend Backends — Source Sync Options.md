---
updated: 2026-10-01
status: Sean HDD runtime verified; Syncthing receiver ready; actual Mac pairing pending
---

# Source sync options

## Current Sean rollout — 2026-10-01 (supersedes source placement and rollout order below)

Sean requested staying with his profile first and using an agent-facing runbook as the primary onboarding interface. See [[Chart Dev — Agent Onboarding]]; canonical source is `~/workspace/chart-infra/docs/AGENT-ONBOARDING.md`. Repository paths are explicit inputs discovered by the laptop agent, not a required `~/workspace` convention. The helper commands are optional building blocks for inspect/pair/resume/wait/pause.

The three active backend checkouts and their Linux dependency caches now run from `/mnt/hdd/shared-dev/profiles/sean/source/workspaces/pilot/` and `dependencies/`. Verified originals and SSD volumes remain retained. Empty source mirrors for pairing are under `source/mirrors/laptop/`. Only small Syncthing device/config state stays on SSD. Mongo and imported workspaces are unchanged; HDD watcher/data/identity checks passed.

A dedicated `chart-sean/syncthing` receiver is running at `100.66.127.115:22001`, with three receive-only folders **paused and unpaired**. Its identity/config/source volumes survived runtime teardown; exclusions, network isolation, existing OpenScape configuration and API health were verified. Actual Mac pairing, transfer/reconnect tests and activation of laptop mirrors still need to run on the Mac. The current apps continue using the working HDD pilot checkouts.

`chart apps select` now supports explicit prepared source/dependency selection with stopped apps, retained old volumes and guarded inputs. Synced sources also require a matching laptop checkpoint and paused owned folders. App/sync/Mongo teardown are currently separate commands; each retains its data/identity/volumes. Named Mongo dataset switching and additional-developer account/RBAC onboarding remain pending. No second developer stack was provisioned for this step.


Source synchronization can keep every repository in the developer’s laptop workspace, so the existing CLI agent uses its normal file tools across the whole task. Mirror only selected backend source into profile-owned PC directories; the Node watchers consume those mirrors. SSH remains useful for runner commands, tests and logs. The current proposal for review is one Syncthing instance per developer profile, with automated first-time pairing. This replaces direct SSH source patching as the preferred proposed workflow; the SSH helper remains for tests, logs and lifecycle. No synchronization has been configured.

Review follow-up: [[Per-profile Frontend Backends — Review Follow-up]]. Fable’s review found pilot blockers. Sean has now asked to resolve the remaining choices; settled decisions are recorded there. This is still a design handoff: proposed commands and runtime checks have not been implemented or verified.

## Existing Syncthing: inspected read-only

- k3s namespace/deployment: `openscape-sync/syncthing`.
- Image: `syncthing/syncthing:2.1.5@sha256:397aa00b92b48d65540ea3ae3cbf271b87bdccbe07a0b7bd7d2debc3a7b29138`.
- State PV host path: `/home/sean/services/openscape-sync/state`, mounted at `/var/syncthing`.
- One configured folder: `openscape-plans`, label “OpenScape plans”, send/receive, staggered versioning, four configured devices on that folder.
- Folder `/plans` maps to `/home/sean/obsidian/vault/Claude Plans/OpenScape`.
- Host networking; GUI on `127.0.0.1:8384`.
- No development source directories are currently mounted in this container.

No configuration, mount, device, folder, credential or running service was modified. The current installation need not be replaced to support source sync. Its state/configuration and Obsidian folder are outside the proposed source-sync scope.

## Options

| Option | Fit for this workflow | Effect on existing Syncthing |
| --- | --- | --- |
| Mutagen over SSH | Designed for source watching; explicit sync sessions per profile/repo; works with existing SSH access | None when configured for separate source directories |
| Syncthing | Viable with laptop send-only and PC receive-only folders; developer device pairing and per-folder configuration needed | Reusing the instance needs new source mounts/config; a separate instance avoids those changes |
| Scripted rsync over SSH | Simple explicit transfer/checkpoint; automatic watch/reconnect/status requires additional tooling | None when configured for separate source directories |

**Earlier option: Mutagen over SSH**, keeping all edits and Git operations on the laptop. It directly addresses the single-agent, cross-repo workflow and can leave the running OpenScape Syncthing completely untouched. Mutagen supports SSH transport and incremental source watching. Use a new empty mirror directory and start with `one-way-safe` so conflicting edits on the PC are reported rather than silently replaced. Per-profile Syncthing is now the proposed direction for review; Mutagen remains an alternative and is not deployed. [Mutagen synchronization](https://mutagen.io/documentation/synchronization/), [SSH transport](https://mutagen.io/documentation/transports/ssh/)

The release page currently lists v0.18.1, dated 2025-02-24. Verify compatibility with the actual laptop OS and Ubuntu 26.04 before adopting/pinning it; no runtime sync test was performed here. [Mutagen releases](https://github.com/mutagen-io/mutagen/releases)

## Syncthing can also work

Use one folder ID per profile/repository, shared only with its owning developer’s laptop. Set the laptop to send-only and the PC to receive-only. Receive-only suppresses propagation of local PC edits; it is not filesystem read-only and does not prevent incoming source deletions. Avoid editing the PC mirror independently. Do not use override/revert controls to conceal unresolved conflicting work. [Syncthing folder types](https://docs.syncthing.net/users/foldertypes.html)

For a setup that leaves the current deployment unchanged, run a separate development Syncthing instance with its own namespace/deployment, state PVC/configuration/device identity, source mounts and non-conflicting network listeners. Never reuse the OpenScape state volume, folder ID or synced path. Reusing the current instance is possible, but would need additive source mounts and folder settings with a reviewed backup/diff; never replace its complete config with a generated minimal file.

## Per-developer-profile Syncthing: viable variant

One Syncthing instance per developer profile is feasible on this host. One instance can manage all that profile’s repository folders; do not create an instance per repository or per Mongo dataset. This is the current proposal for review, not a deployed capability.

An idle measurement on 2026-09-30 showed the existing plans-only Syncthing Pod at **5 millicores and 30 MiB RAM**, with node memory usage around 10.2 GiB. That supports a small initial pilot but is not a source-tree sizing result: file count, hashing, indexing and active transfers can increase consumption. Follow the existing decision to omit profile CPU/memory requests, limits and quota defaults; measure real use with build/dependency directories excluded.

### Per-profile layout

- A dedicated Syncthing controller/Pod in the existing profile namespace, e.g. `dev-alex-fixture`, without `hostNetwork` or host ports.
- Its own persistent state/configuration PVC and independently generated device identity. Following the placement decision recorded in Fable’s review, keep this small state PVC on SSD using the retained SSD class, separate from Mongo dataset directories. Sean superseded the earlier source placement: backend source mirrors and Linux dependency caches now belong on HDD with Mongo; keep their directories separate. Extend the SSD free-space guard to dependency preparation. Preserve it across teardown so laptop pairing survives runtime recreation. Never copy the OpenScape instance’s keys/config/database. [Syncthing configuration and identity](https://docs.syncthing.net/users/config.html)
- Separate registered source folders for auth, tharamine and Orange, shared only with the owner’s laptop/device. The laptop’s one Syncthing client can manage these folders. Use distinct folder IDs and non-overlapping destinations per profile/repo; avoid simultaneously syncing a parent workspace and its nested repos.
- Syncthing mounts the source PVCs read/write, while application Pods mount those same source PVCs read-only. Both consumers run on this single node. Use administrator-provisioned local PVs and check UID/GID ownership; do not mount the full home directory or grant access to other profiles, secrets or Mongo data.
- Laptop send-only and PC receive-only source folders. The existing laptop agent edits all repos locally; source changes arrive in the PC mirror and trigger the application watcher. SSH remains for tests/logs/lifecycle, with no independent patches to the mirror while synchronization owns it.

### Connectivity

Each Pod can use internal TCP port 22000 because Pods have separate network namespaces. For direct laptop connections, allocate one stable **Tailscale-bound TCP endpoint per profile**, forwarding to that profile’s ClusterIP Service, and pin the paired device’s address. Port 22000 on the Tailscale address is already taken by OpenScape. Inspect port availability before assigning different per-profile numbers. This can extend the existing host TCP access design; it does not need public NodePorts or changes to the OpenScape Syncthing listener. GUI/API access stays authenticated and available through scoped SSH/Kubernetes forwarding instead of a new public dashboard.

Initially set the profile instance’s listener explicitly to TCP-only on its Pod port 22000, with public discovery, relays, local discovery, UPnP/NAT traversal, usage reporting and crash reporting disabled for these new instances. Add only the NetworkPolicy rules required for the chosen sync path; keep backend application egress denied. Validate the host-proxy/network-policy path against the real cluster. Do not present port allocation or NetworkPolicy admission as already solved. [Syncthing connection settings](https://docs.syncthing.net/users/config.html)

### First-time setup owns pairing and folder configuration

Provide a laptop-side setup command in the reusable tooling, with a plan/review mode and repeatable non-interactive inputs for agents. It complements operator provisioning on the PC; developers do not need cluster-admin. This is a required implementation behavior, not existing command syntax.

The developer’s one-time inputs are their profile, SSH login and local repository paths, plus authorization to use the laptop Syncthing API. Tailscale and SSH access must already work. If Syncthing is not installed/running locally, guide its installation/start explicitly. Do not infer which instance to modify when multiple local instances/configurations are found.

After operator provisioning, setup should:

1. Verify the PC/cluster/profile identity through the trusted SSH connection. Obtain the profile Syncthing device ID and assigned Tailscale sync endpoint from its inventory. Discover the laptop device ID using its local API; keep API keys on their respective machines, out of command lines, output, synced folders and shared config.
2. Inspect existing laptop folders/devices and ignore files. Record a private before-state and present an additive plan. Refuse colliding folder IDs, overlapping sync roots, non-empty unowned remote targets or unexpected device identities. Already-synced local folders need an explicit reuse/separate-root decision; do not silently register overlapping folders or replace the developer’s existing sync setup.
3. Add only the intended laptop/profile device relationship and profile-owned folder objects on both sides, initially paused. The PC helper performs scoped remote API operations; laptop setup modifies its local instance. Keep folder auto-accept/introducer behavior off for these new device relationships, and do not change the laptop instance’s global discovery/listener settings that other sync jobs may need.
4. Set laptop send-only and PC receive-only for the new folders, with deterministic unique IDs per profile/repo. Configure private direct connectivity on the new profile instance. Apply only granular API changes to owned objects: API PATCH can replace nested arrays, so preserve unrelated members rather than treating it as an automatic append. Never PUT a generated replacement for the entire existing config. Check whether the changes require restart. [Syncthing configuration API](https://docs.syncthing.net/rest/config)
5. Install and verify ignore rules **on both sides before enabling transfers**. `.stignore` itself does not sync, and existing rules must not be overwritten. Exclude Git internals, credentials/private env files, dependency trees, build output and data paths. Use per-folder rules, not global default changes. [Ignore configuration API](https://docs.syncthing.net/rest/db-ignores-post)
6. Unpause the newly configured folders. Verify the intended device connection, completed initial scans/transfers and no folder errors/conflicts. Check a source fingerprint before claiming tests/startup will use the expected files. Exercise a temporary non-secret source probe in both directions as appropriate: laptop changes arrive; remote changes do not propagate back. Remove only the probe created for this check. No existing project data is deleted.
7. Report ready status, source paths, profile endpoint and the daily commands. Re-running setup reconciles the same owned objects without rotating device identity, resetting folders, duplicating entries, weakening exclusions or redoing manual pairing.

The entire pairing flow can be automatic once both API endpoints are available through authorized local/SSH access; the PC cannot configure a laptop it cannot access by itself. A manual GUI acceptance path remains a fallback if the developer declines local API access. Do not quietly fall back to global folder auto-accept or public GUI exposure.

On interruption, leave new folders paused or clearly report partial configuration. Keep an ownership/change record so retry can continue and rollback can remove only newly added entries; never restore a stale whole-config backup over unrelated later changes. Retain device identity and pairing across normal stop/start and data-only teardown. Device replacement/removal is a separate explicitly targeted action.

Acceptance includes a second setup run, interrupted/resumed setup, initial ignore enforcement with synthetic secret-like files, conflicting existing folder detection, and before/after proof that unrelated laptop settings and the PC’s OpenScape sync configuration were preserved. Read-only review and this handoff do not authorize pairing any real laptop now.

### Lifecycle and proof

Normal app stop may leave source sync running. Explicit data-only teardown stops/removes the profile Syncthing runtime too, while retaining its small state/config/key PVC and source directories alongside Mongo volumes and inventory. Normal spin-up restores the same device identity and mounts. Name/document these retained metadata volumes so “data-only” does not accidentally regenerate identities or require re-pairing.

Pilot Sean plus a second profile: prove local-agent source edits trigger the correct backend; no cross-profile folder access; excludes, source deletion and reconnect behavior; pairing survives teardown; the existing OpenScape Deployment, configuration and synced files remain unchanged. No global Syncthing configuration should be rewritten. Treat a failed/conflicted/incomplete sync as a failed source-readiness check before tests or startup.

This variant costs several small daemons and device pairings but gives each profile independent configuration, permissions and lifecycle. It is now the proposed design for review, with first-time setup responsible for pairing and folder configuration. Do not deploy both sync engines onto the same source directory.

### Review-required synchronization checkpoint

Before remote tests/startup claim they use the latest laptop edit, force a laptop scan of each involved folder, then wait for the intended PC peer’s completion with zero outstanding items and no errors/conflicts or receive-only divergence. Compare content fingerprints over the same agreed relative-path/file manifest on both sides, including additions/deletions and excluding sync-internal metadata as well as the configured exclusions. Record laptop HEAD, dirty state and the verified fingerprint in test results. Do not rely on an old “100%” value reported before the file watcher notices a save.

Then verify the application has reloaded that source generation; an already-passing readiness probe may still belong to the old process. Define and prove an observable reload-generation or response check during Phase 0. This is a verification requirement, not an implemented helper.

Surface receive-only changed-file counts in status. A future source-revert operation must preview divergence and require an explicit invocation, stay confined to the selected mirror and never run automatically during setup/start. Reversion can delete PC-local changes; “receive-only” is not a read-only mount for Syncthing itself.

## Requirements whichever tool is selected

- The laptop is the source of truth. Agent edits and Git operations remain local; remote source is a runtime mirror. In this mode, the SSH helper is for tests/logs/lifecycle, not independent source patches.
- Sync only explicitly registered source roots into fresh profile-owned directories, separate from existing worktrees, OpenScape plans, Syncthing state and Mongo datasets. Do not overlap directories managed by different sync engines.
- Exclude `.git`, private `.env` files, `.npmrc`, keys, `node_modules`, build output and caches. Ignore syntax differs between tools: generate and verify the actual rules instead of assuming `.gitignore` is honored. Retain non-secret source/config templates explicitly if required. [Syncthing ignores](https://docs.syncthing.net/users/ignoring.html), [Mutagen ignores](https://mutagen.io/documentation/synchronization/ignores/)
- Keep Linux dependencies outside the source mirror and install from the selected lockfile. No Mongo database files, data volumes or persistent credentials enter synchronization; Mongo data stays on the HDD.
- Ordinary source deletions must reach the disposable mirror. That permission does not extend to dataset deletion. Git/sync safety mechanisms are not a backup of unsynchronized edits.
- Provide start/status/wait-for-sync/pause/stop and visible conflicts/errors, plus profile/repo-specific session names. A test command must confirm pending source transfers completed before claiming it tested current code.
- File sync is not an atomic deployment of an entire cross-repo change. Intermediate watcher restarts can fail temporarily. For incompatible coordinated changes, stop affected applications, complete sync, prepare dependencies/migrations explicitly, then start them.
- Prove edit/create/rename/delete events, reconnect behavior, excluded-path protection, conflicting remote-edit handling, and unchanged Pod/image identity during hot reload. Check both source contents and an observable backend response.
- A pilot must leave the existing OpenScape Syncthing deployment/configuration and its synchronized files untouched. There is no authorization here to migrate or reset that existing setup.
