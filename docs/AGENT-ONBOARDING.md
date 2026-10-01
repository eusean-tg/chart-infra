# Agent runbook: backend source with Mutagen over SSH

Current choice, approved by Sean on 2026-10-01: **Mutagen over existing SSH**, laptop → HDD mirror. This replaces the dedicated source Syncthing design. Existing OpenScape Syncthing must remain unchanged. This runbook is the primary onboarding interface; helper scripts are optional building blocks, with explicit paths and no required laptop workspace convention.

Scope: Sean first. His PC mirrors are registered, the dedicated Syncthing Deployment/Service/policy and port 22001 are retired, and all former state/PVs/PVCs are retained. The applications now run from clean HDD `laptop` mirrors after a verified Mac freeze. The laptop agent reports resumed sessions and successful real backend watcher/exclusion checks; real Tailscale reconnect passed per the laptop report, and Sean reported successful frontend sign-in/save/reload; the `pilot` checkouts/config remain retained for rollback. Other developers need their own provisioned Linux account/profile access first; do not give them Sean's SSH credentials or cluster-admin kubeconfig.

## 1. Inspect both sides

On the PC read `AGENTS.md`, README and `docs/DEPLOYMENT.md` in `~/workspace/chart-infra`. Verify host/cluster/HDD registration and retained Mongo identities. The installed entry point is `./chart`; no old `./dev frontend` commands apply.

For Sean:

```sh
cd /home/sean/workspace/chart-infra
./chart sync status --profile sean
./chart sync export --profile sean
```

Chart commands resolve Kubernetes credentials independently of shell startup: when `KUBECONFIG` is unset they use the invoking user’s `~/.kube/config`; an explicit override is preserved. Do not make `/etc/rancher/k3s/k3s.yaml` readable or source interactive dotfiles as a workaround. `sync status/export` keep registered-cluster/HDD/volume checks but take no app/Mongo/sync lifecycle locks, so they remain available during long installs/rollouts. A concurrent inventory change can fail a read safely; retry after preparation completes.

An operator preparing a **new**, already provisioned profile runs `./chart sync prepare --profile <profile> --workspace laptop` as its registered Linux owner. Sean's migration is already complete; do not repeat legacy migration or regenerate identity. Prepare never fetches repos, copies secrets, installs dependencies or selects source.

On the laptop, identify the actual checkouts of `auth-service-backend`, `tharamine-user-service` and `orange-v2-backend`. Record absolute paths, Git HEAD/dirty state, lockfiles, submodules, existing Mutagen sessions and any other tool syncing these paths. Do not assume `~/workspace`. The earlier laptop report described clean main checkouts there, with `CLAUDE.md -> AGENTS.md`; re-inspect rather than discarding later edits.

Confirm key-based SSH works from the laptop to `sean@100.66.127.115`. Reverse SSH is unnecessary. Keep one existing cross-repo agent on the laptop; no remote AI session/login is required. Frontend code stays on the laptop and is outside these sync sessions.

## 2. Install the verified client and copy helpers

Use **Mutagen 0.18.1** for this tested implementation. Inspect any existing installation/daemon first; do not replace or restart another workflow's daemon without coordinating it. The helper refuses a different version instead of assuming API compatibility. Mutagen's remote helper is installed automatically over SSH; no additional listener or Kubernetes deployment is needed.

For a fresh Mac installation, use the official release archive for its architecture and check SHA-256 before extracting. Example for Apple Silicon:

```sh
mkdir -p "$HOME/.local/share/chart-infra-tools/mutagen-0.18.1"
cd "$HOME/.local/share/chart-infra-tools/mutagen-0.18.1"
curl --fail --location --output mutagen.tar.gz \
  https://github.com/mutagen-io/mutagen/releases/download/v0.18.1/mutagen_darwin_arm64_v0.18.1.tar.gz
printf '%s  %s\n' \
  6f810416d9e5fc4fd5e18431146f8b3c5a2056ba5a24f76c1e66da86eb3257e2 \
  mutagen.tar.gz | shasum -a 256 -c -
# Extract only after the checksum passes; keep mutagen and its agent bundle together.
tar -xzf mutagen.tar.gz
./mutagen version
```

For an Intel Mac, select `mutagen_darwin_amd64_v0.18.1.tar.gz` and checksum `7d06f7d8fcfe90bc7e55cc834a2f2f20c2e0af9ea9bc35911fc4341ad56a9bbf`. Do not run both architecture examples. Release/checksum source: [Mutagen v0.18.1](https://github.com/mutagen-io/mutagen/releases/tag/v0.18.1).

Copy the matching standard-library Python helpers together:

```sh
mkdir -p "$HOME/.local/share/chart-infra-tools"
scp sean@100.66.127.115:/home/sean/workspace/chart-infra/laptop-sync.py \
    sean@100.66.127.115:/home/sean/workspace/chart-infra/sync_common.py \
    "$HOME/.local/share/chart-infra-tools/"
```

A shell convenience function for the remaining examples:

```sh
chart_sync() {
  python3 "$HOME/.local/share/chart-infra-tools/laptop-sync.py" "$@" \
    --host sean@100.66.127.115 \
    --remote-dir /home/sean/workspace/chart-infra --profile sean \
    --mutagen "$HOME/.local/share/chart-infra-tools/mutagen-0.18.1/mutagen"
}
```

Adapt host, profile, remote directory and client binary explicitly for other installations. Python 3 and OpenSSH are required. Paths/config are stored privately in `~/.local/state/chart-infra/mutagen/<profile>/`; no registry, Syncthing API or database tokens are copied.

## 3. Review the plan, then create paused sessions

Substitute the three **actual absolute paths**:

```sh
chart_sync plan \
  --auth /absolute/path/auth-service-backend \
  --tharamine /absolute/path/tharamine-user-service \
  --orange /absolute/path/orange-v2-backend
chart_sync setup \
  --auth /absolute/path/auth-service-backend \
  --tharamine /absolute/path/tharamine-user-service \
  --orange /absolute/path/orange-v2-backend
```

Before creating sessions, ensure the copied helpers match the PC export policy/hash. Sean’s unused registration was upgraded from v1 to v2 without changing its identity. The operator command `./chart sync upgrade-policy --profile sean` supports only that known upgrade on an unused registration with empty mirrors; registered sessions need a separately coordinated migration, never regeneration.

Setup creates one paused `one-way-safe` session per repo. It passes `--no-global-configuration` and an explicit generated policy, leaving other Mutagen sessions/defaults and Syncthing unchanged. A private ownership record and labels permit retry after interruption; repeats verify existing IDs, endpoints, exclusions and ownership rather than replacing them. A missing recorded session or changed mapping requires inspection, not silent recreation. Operations use exact owned session IDs, never `--all`.

The plan rejects overlapping local/remote Mutagen roots and checks standard local Syncthing config paths for overlap without using or modifying its API. Supply `--syncthing-config /actual/config.xml` for a nonstandard instance, and inspect other synchronization tools/SSH aliases manually. Never register two engines against the same mirror. No pairing, source `.stignore` edits or Syncthing GUI credentials are required.

The generated ignore policy is `chart-mutagen-v2`, case-insensitive for its ASCII patterns. It excludes private `.env*`, `.npmrc`, `.netrc`, keys/certificates, Git internals, dependencies, caches and build output. It also excludes `graphify-out`, `.graphifyignore` and the entire `.husky` directory at any depth; these local tooling files have no role in the runtime mirror. Only root `.env.sample`, `.env.example` and `.env.template` are retained; inspect that these are non-secret examples. Never use actual keys as exclusion probes. `.stignore`/`.stfolder` retained from the earlier preparation remain excluded and untouched.

Mutagen ignores symlinks. Our source validation explicitly permits omitting only the known root `CLAUDE.md -> AGENTS.md` alias; `AGENTS.md` itself transfers. Other included symlinks are rejected by preflight/checkpoints for review, not silently accepted as a complete source tree. Do not add blanket exceptions if a build needs a symlink.

Remote source paths remain:

```text
/mnt/hdd/shared-dev/profiles/<profile>/source/mirrors/laptop/<repo>
```

Mutagen's remote staging is `neighboring`, beside the mirror on the HDD. Files/directories default to owner-only access (0600/0700, with executable handling by Mutagen); Sean's runtime uses the same UID 1000. Future profiles must align their own SSH owner and Pod UID rather than making mirrors world-writable. Their Linux dependencies stay separately under `dependencies/<repo>/<input-hash>/node_modules`. Apps mount mirrors and dependencies read-only. Mongo/keys/backups are never sync roots.

## 4. Synchronize unmodified source

```sh
chart_sync resume
chart_sync wait
```

`wait` validates local source, forces a full synchronization cycle, checks connected/clean sessions, compares content fingerprints on the PC and records laptop HEAD/dirty state. Stop editing while verifying; retry if the source changes during the check. A clean session alone is insufficient: `one-way-safe` can retain non-conflicting PC-only files, which the fingerprint check rejects. Inspect conflicts instead of resetting/overwriting the mirror.

Use unmodified backend source; no chart-infra patch is required. Preserve developer changes rather than resetting them, and record HEAD/dirty state accurately. Do not edit PC mirrors. Private runtime config/keys remain separate PC mounts, never transferred from laptop `.env.local`/PEM files.

Stock Tharamine starts its normal worker entrypoints. Supported development settings still disable external integrations, and app NetworkPolicies restrict egress to this profile's services and cluster DNS. Missing credentials alone is not an egress control. The old global worker-disable and Mongo wait-queue overrides are absent from the selected sync configuration; stock Tharamine uses its 10000 ms wait-queue timeout. A startup failure needs diagnosis or an upstream fix, never an unreviewed mirror patch.

For dependency preparation and selection, use:

```sh
chart_sync freeze
```

`freeze` flushes, checks errors/conflicts, pauses all three owned sessions and records a matching paused checkpoint. It leaves them paused on a verification failure. After fixing an issue, `resume` then `freeze` again. Plain `pause` is for stopping transfer and does not establish an activation checkpoint. Resume invalidates the checkpoint before allowing writes. The PC records the laptop's pause attestation and independently verifies filesystem fingerprints; it cannot query the laptop daemon itself. Use the helper rather than raw Mutagen resume commands, and do not edit during activation.

On the PC, from chart-infra (or through the same SSH session):

```sh
./chart deps install --profile sean --workspace laptop --service auth-service-backend
./chart deps install --profile sean --workspace laptop --service tharamine-user-service
./chart deps install --profile sean --workspace laptop --service orange-v2-backend
```

Linux dependencies are prepared from frozen mirrored lockfiles, with private `~/.npmrc` mounted only to the installer. Existing immutable cache generations are reused when inputs match. No Mac `node_modules`, automatic package upgrade, source fetch or migration fleet belongs in this step.

## 5. Activate explicitly, preserving current data

With sessions still frozen:

```sh
./chart apps down --profile sean
./chart apps select --profile sean --workspace laptop
./chart apps up --profile sean
```

Selection verifies registered paths/volume identities, fingerprints, prepared dependencies/runtime. It retains prior source/dependency/config volumes and selection history. Selecting synced source uses a retained configuration generation without the two obsolete patch-only flags; selecting `pilot` restores its original config. It does not change Mongo data, regenerate keys or run migrations. If it fails, inspect the reason; do not delete PVCs. To return to the previous prepared checkout while stopped, select workspace `pilot` and start it again.

Verify `http://100.66.127.115:13000/api/v1/health/services`, then run `chart_sync resume` on the laptop. Ordinary edits now reach the mounted source and trigger `tsx watch`. The frontend remains configured using [the daily guide](GETTING-STARTED.md); no frontend hostname, callback or TLS redesign is part of this transport change.

For incompatible cross-repo edits or dependency changes, stop the affected apps before syncing, freeze, explicitly prepare dependencies/migrations and select again. Synchronization is not an atomic cross-repo release.

## 6. Actual Mac acceptance and daily use

The PC-only SSH fixture verified real transfer/exclusions, three mounted watchers, atomic saves/create/rename/delete, conflict retention, extra-file rejection, interrupted/repeated setup and daemon reconnect. **That is not actual Mac acceptance.** Record separately:

1. All three real repo fingerprints match after `wait`; no excluded synthetic secret/dependency probes arrive.
2. A controlled edit in each backend executes a unique marker/behavior with unchanged Pod UID/image; restore it from the laptop.
3. Laptop disconnect/reconnect recovers, and `status`/`wait` report correct connectivity and content.
4. Repeating setup preserves session IDs and unrelated Mutagen/Syncthing settings. Do not stop a shared laptop daemon merely to test reconnect.
5. The frontend can still sign in and save/reload a workspace with unchanged development identities/data.

Daily commands are `chart_sync status`, `wait`, `pause`, `resume` and `freeze`. App logs/start/stop remain SSH commands from the daily guide. App teardown does not stop source transfer: pause owned sessions separately. For full runtime suspension, pause source sessions, stop apps and optionally stop Mongo; the app/Mongo `--data-only` modes retain all storage and identities. No PC `chart sync up/down`, new sync listener or dedicated source-sync Deployment is needed.

No session termination, state/PVC deletion or forced conflict reset occurs automatically. Retained Syncthing identity/state can support a separately reviewed rollback, but never restart it while Mutagen owns the same mirrors. The legacy adapter refuses restart after a Mutagen registration exists.

### Laptop reboot, login and daemon startup

Sean has chosen **on-demand startup**, with no launchd registration. Sessions remain saved when the daemon stops, but no files transfer until it runs again. After reboot or logging back in, connect Tailscale and run on the laptop:

```sh
chart_sync status
chart_sync wait
```

The helper's session query starts the daemon when absent, unless `MUTAGEN_DISABLE_AUTOSTART=1`. Its PC preflight runs first, so restore SSH/Tailscale reachability first. Offline edits are reconciled once synchronization runs; inspect any reported conflicts. `status`/`wait` do not resume paused or frozen sessions. If those sessions should now run, use `chart_sync resume`, then `chart_sync wait`; keep an intentional activation freeze in place until deployment is complete. See [Mutagen daemon lifecycle](https://mutagen.io/documentation/introduction/daemon/).

**Optional login startup — not enabled for Sean:** Mutagen supports `mutagen daemon register` on macOS. Coordinate first if this per-user daemon serves another workflow. The pinned implementation requires the daemon to be stopped for registration changes. An agent implementing a developer's choice would stop that daemon, register using the selected binary and start it:

```sh
"$HOME/.local/share/chart-infra-tools/mutagen-0.18.1/mutagen" daemon stop
"$HOME/.local/share/chart-infra-tools/mutagen-0.18.1/mutagen" daemon register
"$HOME/.local/share/chart-infra-tools/mutagen-0.18.1/mutagen" daemon start
```

The launchd plist records that executable path. A move to another versioned path requires coordinated stop, unregister and registration using the new binary, followed by start; simply registering again leaves an existing entry unchanged. Inspect the plist and update the reviewed client/helper version pins together. Registration is per user, not per profile, and does not resume paused sessions. See [Mutagen 0.18.1 macOS registration implementation](https://github.com/mutagen-io/mutagen/blob/v0.18.1/pkg/daemon/register_darwin.go). This guide does not register, stop or upgrade anyone's daemon automatically.

## 7. Optional backend .env.local import — agent procedure

Developers may bring their own **development** `.env.local` settings. Their agent performs this procedure; there is no import script or bulk environment replacement command. Discover actual file paths and service ownership rather than assuming a laptop directory convention. Frontend `.env.local` stays on the laptop; this procedure concerns the three PC backend services.

### Inspect and plan

1. Identify the developer's Linux owner, prepared profile, selected workspace and service (`auth`, `tharamine` or `orange`). Verify the registered host/cluster/HDD and inspect current `identity/apps/inventory.json`, including `config_paths` and volume identities. New developer accounts/RBAC still need operator provisioning; do not clone Sean's credentials or identities. This import does not provision an account or an entire app profile.
2. Ask for the file's **path**, never its contents in chat. Inspect it privately with a dotenv-aware parser. The pinned Node 24 runtime provides `node:util.parseEnv`, verified with quoted and multiline fixtures. Do not execute/source the file as shell code or split it naively at every `=`. If values rely on interpolation or dotenvx encryption, resolve their intended development values explicitly; do not assume the parser expands variables or supplies decryption keys. Never use production credentials.
3. Compare names and intended behavior against the **actual checked-out source**, current runtime config and the developer's requested feature. `.env.sample` is a starting point, not an exhaustive schema: the auth backend-key setting was absent from its sample. Prepare a report of key names to import, keep, adapt or omit, with reasons; do not show secret values or a raw config diff. The requested import authorizes ordinary development settings. Ask only about unresolved intent or an actual identity change, not every variable.

| Kind of setting | Agent treatment |
|---|---|
| Application preferences and local feature settings | Import the requested values after checking their use in this revision. Absent variables retain current values; absence is not a deletion request. |
| Browser origins, frontend URLs and cookies | Adapt to this developer's actual laptop frontend and proxy setup. Keep the backend-to-backend addresses separate. |
| Mongo, Redis and internal auth/user-service addresses or credentials | Keep the profile's generated values and cluster DNS. Laptop `localhost` refers to the Pod after deployment; another developer's or production DB is not a substitute. |
| Signing keys, key paths, service-auth registry, session secrets and encryption keys | Preserve the profile's retained identities. Translate a requested path only to an existing matching PC-mounted development key. Use `apps prepare-backend-key` for the missing auth backend key. Replacing an existing identity is a separate explicit developer decision; retain the old files and explain session/token consequences. |
| Runtime/environment, listen ports, telemetry, external integrations, proxy and Node loader options | Keep the runner's development and offline settings unless a separately authorized environment change covers them. An env import does not enable live capture, external refresh/history, delivery, production endpoints or network-policy changes. |
| npm/Git credentials | Keep them in private installer/source-fetch configuration, outside the app environment and runtime mounts. |
| Unknown variables | Read their consumers before deciding. Do not silently import a key that points to an unreviewed service or changes runtime behavior. |

### Transfer and apply privately

4. If a PC copy is needed, use SSH/SCP to a **new** owner-only directory under `/mnt/hdd/shared-dev/profiles/<profile>/identity/env-imports/<service>/<timestamp>/`. Use directories `0700` and files `0600`; keep the raw file outside the mounted service config directory, source mirrors, Git and Obsidian. Retain prior imports. Use only the developer's prepared account/profile and do not put values into command arguments, logs or chat. Mutagen's `.env*`/key exclusions stay in place; importing an env file does not mean syncing it continuously.
5. For an existing running profile, have the laptop agent freeze its owned source sessions, then run `./chart apps down --profile <profile>`. Leave Mongo/data running. For initial setup, apply the reviewed settings after app identities have been prepared and before first app startup. Importing configuration does not fetch source, install dependencies, run migrations or reset data.
6. Before editing, use chart-infra's registered-host/HDD guards and app/Mongo lifecycle locks, as other mutating operations do. Save a private copy of each original config and a values-free receipt under `identity/env-imports/<service>/<timestamp>/`. Record selected paths, volume UIDs and existing key fingerprints. Merge only the reviewed variables into the canonical protected `identity/apps/<service>/config.json`, using a JSON-aware writer and atomic replacement with mode `0600`. Preserve all unrelated entries and key files. Release these locks before invoking a chart command that acquires them itself.
7. **Do not modify `identity/apps/runtime-configs/...` directly.** These are retained generated configurations. With the same source checkpoint still frozen, run `./chart apps select --profile <profile> --workspace <selected-workspace>` to prepare/select the appropriate configuration generation from the updated canonical config. It will also check dependency inputs. A mismatch requires the normal explicit dependency-preparation workflow, not a bypass. Then run `./chart apps up --profile <profile>`.

### Verify and hand back

8. Check service readiness and API health, inspect new logs privately for missing-variable/key errors, and verify the requested feature using local fixtures. Inspect the effective mounted config without printing secrets. Confirm the right profile DB/internal services, retained data/identities, source exclusions and existing external-egress restrictions. For an import that affects workers or destinations, repeat `python3 tests/app_egress.py --profile <profile>` and review its limited scope; do not contact live APIs as a test.
9. Once startup is healthy, have the laptop agent resume its owned sessions and verify synchronization. Record which key names were imported/adapted/retained, the private backup/receipt locations and verification results. Actual secret values remain only in protected private files. Other developers' profiles are unaffected.
10. If verification fails, keep the apps stopped while restoring the backed-up canonical config, select the same workspace again and start it. Reuse the original retained keys/data; retain the rejected config/import/generation for inspection. Do not delete source, databases, identities or PVCs to fix an env problem. Resume sessions after recovery.

Importing a developer's own settings is supported through this agent procedure. No developer env file has been imported merely by documenting it, and there is no promise that an arbitrary laptop file works unchanged inside a Pod.

## 8. Known onboarding issues and acceptance checks

These findings apply to future profiles too; inspect them during onboarding rather than requiring developers to rediscover Sean's setup history.

| Symptom or check | Action |
|---|---|
| Non-interactive SSH `kubectl` reports permission denied on `/etc/rancher/k3s/k3s.yaml` | Chart commands already default to the invoking user's `~/.kube/config`. Direct kubectl needs `KUBECONFIG=$HOME/.kube/config`; do not loosen server kubeconfig permissions or distribute cluster-admin credentials. |
| Private npm installs fail although GitLab SSH works | Configure that PC account's private `~/.npmrc` with correct registry paths and mode `0600`; keep tokens out of runtime/source sync. Go module HTTPS discovery auth is separate and belongs to the Go workspace setup. |
| Auth logs `Error reading private key backend` | Run `apps prepare-backend-key --profile <profile>` and restart auth. New `apps prepare` provisions it. Check `PRIVATE_KEY_PATH_BACKEND` points to the retained mounted key, even if `.env.sample` omits it. Do not reuse another profile's key. |
| Local dotenv settings do not appear in a Pod | The launcher loads protected `config.json`; laptop dotenv/PEM files are intentionally excluded. Use section 7's private import procedure. |
| Dependency mismatch or unprepared Linux packages | Freeze source, explicitly prepare Linux dependencies from its lockfiles and select the workspace while stopped. Mac `node_modules` never transfers. |
| Selection refuses a checkpoint | Run helper `freeze`, which flushes, compares files and pauses the owned sessions. Plain pause or manually resuming sessions is not an activation checkpoint. Inspect conflicts/extra PC files rather than resetting a mirror. |
| Unexpected symlink or tooling output affects sync | Use the matching v2 helper pair/policy. Graphify/Husky and private files are excluded; the known root CLAUDE alias is supported. Inspect other required symlinks rather than silently omitting them. |
| Stock Tharamine startup times out on Mongo | Record the actual failure and disk/database conditions. No local source patch is required. Sean passed a restarted-Mongo test at 10000 ms, but this is not a guarantee for every dataset; diagnose, warm the database if appropriate or pursue an upstream fix. |
| Hot reload works but browser sign-in fails | Check the laptop Vite proxy, origins and cookie settings in the daily guide; successful service health alone does not prove the frontend flow. |
| New developer profile considered ready | Verify source exclusion/fingerprints, edits/restoration in all three actual backends, unchanged Pod/image during watcher reload, profile isolation, reconnect, frontend sign-in and workspace save/reload. Record agent-observed versus developer-reported results. |

Sean's laptop agent verified source lifecycle/exclusions, all three real watchers and recovery after killing only Mutagen SSH transports. The later handoff also records Sean disconnecting Tailscale, making an offline edit and reconnecting, followed by a successful `wait`. Frontend sign-in and workspace save/reload passed according to Sean; an agent did not independently observe the browser flow. A second full application profile and Linux-account/RBAC provisioning remain separate unfinished work.

## References

- [Mutagen SSH transport](https://mutagen.io/documentation/transports/ssh/)
- [One-way-safe semantics](https://mutagen.io/documentation/synchronization/)
- [Ignore matching](https://mutagen.io/documentation/synchronization/ignores/)
- [Symbolic links](https://mutagen.io/documentation/synchronization/symbolic-links/)
- [HDD-local staging](https://mutagen.io/documentation/synchronization/staging/)
