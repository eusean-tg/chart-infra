# Agent runbook: pair a developer's backend source

This is the primary setup interface. Commands are small building blocks; inspect the developer's actual environment and adapt paths explicitly. Do not assume `~/workspace`, a particular Syncthing config path, or that a repository is not already synced.

Current rollout scope: **Sean only**. His imported Mongo data and application identities must remain intact. Separate Linux account/RBAC provisioning and rollout to other profiles are still separate work. Do not give another developer Sean's SSH credentials or a cluster-admin kubeconfig.

## Ownership and safety contract

The laptop is the authoritative source checkout. Its existing cross-repo agent edits files and runs Git locally. Syncthing transfers selected backend sources; SSH handles PC lifecycle/logs/tests. No remote agent process or AI login is needed.

Each PC profile has its own Syncthing device identity and three source folders. Laptop folders are send-only; PC mirrors are receive-only. Applications mount mirrors read-only. Do not independently edit an active mirror, automatically revert conflicts, or use “Override Changes” to hide a bad setup. Ordinary source deletions propagate; Mongo data, identities and backups are outside sync.

Keep the existing OpenScape Syncthing unchanged. Configure only explicitly owned devices/folders using granular API operations; never PUT an entire replacement config, change global discovery/listener defaults on the laptop, auto-accept folders or register overlapping roots. `.stignore` is local to each machine and must be installed before transfers begin. Existing differing ignore files require review; the helper refuses to overwrite them.

API keys stay on their own machines. Setup reads the local config to authenticate over loopback. The laptop initiates SSH into the PC, so reverse SSH from the PC to the laptop is unnecessary. No GUI/API listener is published on Tailscale/LAN. The PC's dedicated source-transfer TCP endpoint is Tailscale-only; discovery, relays, NAT traversal, usage/crash reporting and automatic upgrades are disabled on that new instance.

## 1. Inspect the laptop and choose paths

Confirm with the developer which running Syncthing instance to use. Locate its `config.xml` without printing its contents/API key. The helper recognizes the standard macOS path only if exactly one standard config exists; pass `--config` explicitly for other layouts or multiple instances. Existing HTTPS GUI certificate verification is not bypassed.

Identify absolute paths for these repositories, regardless of directory names:

- auth-service-backend
- tharamine-user-service
- orange-v2-backend

Each selected root must contain `package.json` and `src/index.ts`. Inspect local Git HEAD/dirty state, lockfiles, submodules, existing sync roots and ignore rules. Repositories nested under an already-synced workspace need an explicit reuse/separate-root design; this helper deliberately refuses overlaps. It also refuses included symlinks and conflict files rather than guessing how to expose outside files.

Obtain the PC's SSH destination, profile and absolute chart-infra directory from the operator. For Sean these are `sean@100.66.127.115`, `sean`, and `/home/sean/workspace/chart-infra`. These are this PC's values, not conventions to impose on other developers.

## 2. PC preparation (operator side)

Read `AGENTS.md`, `README.md` and `docs/DEPLOYMENT.md`. Confirm the registered development cluster, HDD identity and existing Mongo/profile inventory. Do not fetch source or initialize replacement data as part of sync setup.

```sh
cd /home/sean/workspace/chart-infra
./chart sync prepare --profile sean --workspace laptop --port 22001
./chart sync up --profile sean
./chart sync export --profile sean
```

Prepare creates a separate persistent device identity and empty mirrors. Up creates only this capability's resources and leaves unpaired folders paused. Repeated prepare checks the existing identity/selection. Missing/replaced state, PVs/PVCs or mirrors must fail, not regenerate.

Backend source and dependency files belong on HDD:

```text
/mnt/hdd/shared-dev/profiles/<profile>/source/workspaces/<workspace>/<repo>
/mnt/hdd/shared-dev/profiles/<profile>/source/mirrors/<workspace>/<repo>
/mnt/hdd/shared-dev/profiles/<profile>/dependencies/<repo>/<input-hash>/node_modules
```

Small Syncthing device/config/database state remains under `~/.local/share/chart-infra/sync/<profile>/state` on SSD, mounted through its own retained PV/PVC. Mongo stays in the separate existing `mongo/default/member-0` directory on HDD. Syncthing cannot mount Mongo, application keys or npm credentials.

## 3. Run inspection and pairing from the laptop

Copy `laptop-sync.py` and `sync_common.py` together into a chosen local tools directory. These need Python 3 and the existing SSH client, not a new Node/pnpm install. Example transfer from the laptop:

```sh
mkdir -p "$HOME/.local/share/chart-infra-tools"
scp sean@100.66.127.115:/home/sean/workspace/chart-infra/laptop-sync.py \
    sean@100.66.127.115:/home/sean/workspace/chart-infra/sync_common.py \
    "$HOME/.local/share/chart-infra-tools/"
```

Use actual paths in the plan command. Sean suggested `~/workspace` as the parent; confirm the individual directories exist first.

```sh
python3 "$HOME/.local/share/chart-infra-tools/laptop-sync.py" plan \
  --host sean@100.66.127.115 --profile sean \
  --remote-dir /home/sean/workspace/chart-infra \
  --auth /absolute/path/to/auth-service-backend \
  --tharamine /absolute/path/to/tharamine-user-service \
  --orange /absolute/path/to/orange-v2-backend
```

Inspect the printed device identity, direct endpoint, folder IDs and paths. `plan` does not mutate Syncthing configuration. Then repeat the same command with `pair` instead of `plan`. Pair captures a private before-state, installs exclusions, adds only owned objects and leaves all new folders paused. It checks unrelated configuration before/after. Repeating pair reconciles the same identity/folders; it does not rotate credentials or duplicate entries. If interrupted, inspect local `~/.local/state/chart-infra/laptop-sync/<profile>/pairing.json` and rerun pair; do not reset either installation.

After successful pair, the path mappings are retained; resume/wait need only host/profile/remote-dir (and `--config` if nonstandard):

```sh
python3 "$HOME/.local/share/chart-infra-tools/laptop-sync.py" resume \
  --host sean@100.66.127.115 --profile sean --remote-dir /home/sean/workspace/chart-infra
python3 "$HOME/.local/share/chart-infra-tools/laptop-sync.py" wait \
  --host sean@100.66.127.115 --profile sean --remote-dir /home/sean/workspace/chart-infra
```

Wait forces a fresh laptop scan, checks the intended PC peer, requires no errors/pending items/receive-only divergence, and compares file-content fingerprints. It records laptop HEAD/dirty state without syncing `.git`. An old “100%” GUI reading alone is insufficient proof that the current edit reached the backend.

## 4. Prepare the source for this offline runtime

Before first activation, inspect the reviewed patch `patches/tharamine-offline-development.patch` against the actual laptop revision. The required worker guard and explicit HDD connection-pool timeout belong in the laptop checkout so sync does not overwrite them. Apply a reviewed compatible patch there; never force-apply/reset conflicting local work or independently patch the receive-only mirror. The worker flag must remain development-only; do not disable workers in production/staging.

After any required edit, repeat wait. Pause the owned folders on both sides while preparing dependencies and changing source selection. Existing OpenScape folders remain running:

```sh
python3 "$HOME/.local/share/chart-infra-tools/laptop-sync.py" pause \
  --host sean@100.66.127.115 --profile sean --remote-dir /home/sean/workspace/chart-infra
```

On the PC, install dependencies explicitly from the mirrored lockfiles:

```sh
./chart deps install --profile sean --workspace laptop --service auth-service-backend
./chart deps install --profile sean --workspace laptop --service tharamine-user-service
./chart deps install --profile sean --workspace laptop --service orange-v2-backend
```

Mac `node_modules` must never sync into Linux. The PC's private `~/.npmrc` is installer-only; no token goes into the source mirror, image or application runtime. Dependency inputs/runtime identity determine cache selection. Do not run automatic package upgrades, migration fleets or source fetches.

## 5. Activate explicitly, with the current data retained

```sh
./chart apps down --profile sean
./chart apps select --profile sean --workspace laptop
./chart apps up --profile sean
```

Selection checks the current source fingerprint, dependency hashes and offline worker guard. It uses new source/dependency volume references while retaining old volumes and a prior-selection record. It does not rebind existing PVs, change Mongo, regenerate keys or install dependencies. If validation fails, inspect the error and keep the previous inventory; do not delete PVCs to make it pass.

After health is good, resume the paired folders from the laptop. Ordinary TypeScript edits can now trigger `tsx watch`. For changes to package manifests/lockfiles, explicitly pause, prepare dependencies and select again; a sync completion alone does not activate dependencies.

## 6. Acceptance and daily operation

Prove on the actual laptop, recording which checks are automated versus user-reported:

1. Create/edit/rename/delete a dedicated harmless source probe and observe the exact expected changes on the PC; remove only the owned probe.
2. Use synthetic secret-like files under excluded paths to verify they never arrive. Never use real credentials as probes.
3. Make a controlled backend edit and observe the changed backend behavior/process marker without a Pod/image replacement; restore that edit from the laptop.
4. Check both device/folder status and matching fingerprints after reconnect. Treat conflict copies, local PC edits and pending transfers as failures; no auto-revert.
5. Repeat pairing to prove idempotence; verify unrelated Mac settings and OpenScape PC configuration remain unchanged.
6. Stop/recreate the dedicated sync runtime and confirm the same device ID, peer/folders and retained source. Do not cycle Mongo for this test.

Use laptop helper `status`, `wait`, `pause`, `resume` as separate building blocks. SSH into the same PC account for app logs/tests/lifecycle; the cross-repo agent remains on the laptop. Sync is not an atomic cross-repo release: for incompatible changes, stop affected apps while synchronizing, prepare dependencies/migrations explicitly, then start.

PC sync lifecycle:

```sh
./chart sync status --profile sean
./chart sync down --profile sean
./chart sync down --profile sean --data-only
./chart sync up --profile sean
```

These preserve identity, pairing, source and PV/PVCs. App down and sync down are currently separate commands. For full runtime teardown, stop sync, stop apps, then stop Mongo; use each command's `--data-only` if controllers should also be removed. No dataset-delete command is introduced. Application deployment, source synchronization, dependency preparation and future live capture remain separate.

## Documentation references

Use the actual running Syncthing version/API as ground truth:

- [Folder types](https://docs.syncthing.net/users/foldertypes.html)
- [Granular configuration API and replacement semantics](https://docs.syncthing.net/rest/config.html)
- [Ignore rules](https://docs.syncthing.net/users/ignoring.html)
- [Folder status](https://docs.syncthing.net/rest/db-status-get.html) and [peer completion](https://docs.syncthing.net/rest/db-completion-get.html)
