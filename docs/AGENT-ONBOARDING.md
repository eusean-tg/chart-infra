# Agent onboarding

Use this workflow to connect a developer's laptop backend checkouts to their prepared chart-infra profile. The coding agent stays on the laptop and can make cross-repository edits. Mutagen transfers backend source over SSH; the frontend runs locally. Discover paths explicitly instead of imposing a laptop workspace convention.

## 1. Establish the profile and access

Read [AGENTS.md](../AGENTS.md) and [Operations](OPERATIONS.md). Obtain the developer's profile name, Linux account, SSH host, PC checkout path and API address from the operator. Inspect both sides before changing anything:

- Confirm the PC targets the registered cluster and HDD. Inspect the profile's inventories, existing data and selected workspace without printing private config values.
- Confirm non-interactive key-based SSH works from the laptop. Reverse SSH and a remote AI-agent login are unnecessary.
- Identify the absolute paths, Git HEAD/dirty state, lockfiles and required symlinks of `auth-service-backend`, `tharamine-user-service` and `orange-v2-backend`.
- Inspect existing Mutagen sessions and other tools syncing these paths. Preserve developer edits, unrelated sessions and global sync configuration.

Developer Linux accounts, filesystem ownership and scoped Kubernetes access require operator provisioning. There is no account/RBAC bootstrap command. Do not give developers another person's SSH credentials or cluster-admin kubeconfig. The profile name must match the Linux account for initial mirror preparation, and Pod/source ownership follows the invoking UID/GID.

Use explicit variables in the PC shell:

```sh
CHART_PROFILE=your-profile
CHART_WORKSPACE=laptop
./chart mongo status --profile "$CHART_PROFILE"
./chart sync status --profile "$CHART_PROFILE"
./chart sync export --profile "$CHART_PROFILE"
```

A missing sync inventory means the profile has not registered mirrors. After the operator prepares Mongo and its storage, register them as the profile owner:

```sh
./chart sync prepare --profile "$CHART_PROFILE" --workspace "$CHART_WORKSPACE"
```

Preparation creates mirrors and retained volume identities; it does not create laptop sessions, fetch source, install dependencies or select apps. An existing registration is reused only when its identity and workspace match. Never delete registration or volume files to bypass a refusal.

Chart commands use the invoking user's `~/.kube/config` when `KUBECONFIG` is unset, preserving explicit overrides. Do not change `/etc/rancher/k3s/k3s.yaml` permissions or source interactive shell files as a workaround. `sync status/export` verify cluster/HDD/volume identity without lifecycle locks; retry a safely failed read after concurrent preparation finishes.

## 2. Prepare the laptop tools

Use Mutagen **0.18.1**, Python 3 and OpenSSH. Inspect the existing binary and daemon before installing anything; the helper rejects incompatible versions. Coordinate daemon-wide changes with other workflows. The remote Mutagen helper installs over SSH and requires no listener or Kubernetes deployment.

For a fresh Apple Silicon installation:

```sh
mkdir -p "$HOME/.local/share/chart-infra-tools/mutagen-0.18.1"
cd "$HOME/.local/share/chart-infra-tools/mutagen-0.18.1"
curl --fail --location --output mutagen.tar.gz \
  https://github.com/mutagen-io/mutagen/releases/download/v0.18.1/mutagen_darwin_arm64_v0.18.1.tar.gz
printf '%s  %s\n' \
  6f810416d9e5fc4fd5e18431146f8b3c5a2056ba5a24f76c1e66da86eb3257e2 \
  mutagen.tar.gz | shasum -a 256 -c -
```

Extract only after the checksum passes, keeping `mutagen` and its agent bundle together, then run `./mutagen version`. For Intel macOS, use `mutagen_darwin_amd64_v0.18.1.tar.gz` with SHA-256 `7d06f7d8fcfe90bc7e55cc834a2f2f20c2e0af9ea9bc35911fc4341ad56a9bbf`. [Release source](https://github.com/mutagen-io/mutagen/releases/tag/v0.18.1).

Copy `laptop-sync.py` and `sync_common.py` together from the PC checkout to a private laptop tools directory. Preserve any existing helper version used by another setup. These files use only the Python standard library and must match the PC's exported policy/hash.

Configure a shell function with actual values:

```sh
CHART_SSH=your-user@your-tailscale-host
CHART_REMOTE_DIR=/absolute/pc/path/chart-infra
CHART_PROFILE=your-profile
CHART_TOOLS="$HOME/.local/share/chart-infra-tools"
chart_sync() {
  python3 "$CHART_TOOLS/laptop-sync.py" "$@" \
    --host "$CHART_SSH" --remote-dir "$CHART_REMOTE_DIR" \
    --profile "$CHART_PROFILE" \
    --mutagen "$CHART_TOOLS/mutagen-0.18.1/mutagen"
}
```

Keep these values available in the shell where the function runs. Laptop ownership records live in `~/.local/state/chart-infra/mutagen/<profile>/`; they contain exact session IDs and mappings. No database or registry token is part of this setup.

## 3. Plan, synchronize and freeze

Provide the three actual absolute checkout paths:

```sh
chart_sync plan \
  --auth /absolute/path/auth-service-backend \
  --tharamine /absolute/path/tharamine-user-service \
  --orange /absolute/path/orange-v2-backend
chart_sync setup \
  --auth /absolute/path/auth-service-backend \
  --tharamine /absolute/path/tharamine-user-service \
  --orange /absolute/path/orange-v2-backend
chart_sync resume
chart_sync wait
```

Plan checks overlapping Mutagen roots and standard Syncthing config paths without modifying them. For a nonstandard Syncthing installation, supply `--syncthing-config /absolute/config.xml`; inspect other tools and SSH aliases manually. Two transfer engines must never write the same mirror.

Setup creates one paused `one-way-safe` session per repository using an explicit policy and `--no-global-configuration`. Retries verify recorded IDs, endpoints, exclusions and ownership. Missing sessions or changed mappings require inspection, not silent recreation. Helpers target exact owned IDs, never all sessions.

Policy `chart-mutagen-v2` excludes private `.env*`, `.npmrc`, `.netrc`, keys/certificates, Git internals, dependencies, caches and build output. It also excludes `graphify-out`, `.graphifyignore` and `.husky` at any depth. Root `.env.sample`, `.env.example` and `.env.template` are allowed; inspect them for secrets. Use synthetic values for exclusion tests.

Mutagen ignores symlinks. Source validation permits omission of the root `CLAUDE.md -> AGENTS.md` alias while transferring `AGENTS.md`. Other included symlinks fail validation and require review. Do not silently omit a link needed by the build.

Mirrors live at `/mnt/hdd/shared-dev/profiles/<profile>/source/mirrors/<workspace>/<repo>`. Remote staging is beside them on the HDD. Default file/directory permissions are owner-only; align SSH owner and Pod UID rather than making roots world-writable. Apps mount source and Linux dependencies read-only. Mongo, identities and backups are never sync roots.

`wait` validates connected, clean sessions and compares content fingerprints with the PC, recording laptop HEAD/dirty state. Stop editing during this check. One-way-safe can retain extra PC files; fingerprint comparison rejects those even if Mutagen reports no conflict. Inspect discrepancies instead of resetting mirrors.

Before dependency preparation or source selection:

```sh
chart_sync freeze
```

Freeze flushes, checks conflicts/errors, pauses owned sessions and records a matching checkpoint. A failure after pausing leaves sessions paused. To retry after diagnosis, resume and freeze again. Plain pause is not an activation checkpoint. The PC verifies files and records the laptop's pause attestation; it cannot query the laptop daemon. Do not resume through raw Mutagen commands or edit during activation.

## 4. Install dependencies and select source

Keep sessions frozen. On the PC, in chart-infra:

```sh
for service in auth-service-backend tharamine-user-service orange-v2-backend; do
  ./chart deps install --profile "$CHART_PROFILE" --workspace "$CHART_WORKSPACE" --service "$service"
done
```

This requires the operator's prepared runtime image and private mode-0600 `~/.npmrc`. Linux installs use frozen lockfiles; Mac `node_modules` never transfers. Matching immutable cache generations are reused. Package updates, source fetching and migration execution are separate work.

For a profile with no app inventory, the operator first runs `./chart apps prepare --profile "$CHART_PROFILE" --workspace "$CHART_WORKSPACE" --port PROFILE_API_PORT`, using its reserved unused port. Preparation generates retained development identities and configuration. It is not a general application-data bootstrap: compatible roles/data and first browser sign-in require separate validation.

For an existing prepared app profile:

```sh
./chart apps down --profile "$CHART_PROFILE"
./chart apps select --profile "$CHART_PROFILE" --workspace "$CHART_WORKSPACE"
./chart apps up --profile "$CHART_PROFILE"
```

For first activation, run `apps select` and `apps up` after `apps prepare`. Selection checks retained identities, dependency inputs/runtime and frozen source fingerprints. Previous generations remain available. Synced Tharamine uses a generated configuration derived from the canonical configuration, omitting legacy patch-only worker and timeout flags. Do not edit generated config directories.

Use unmodified backend source. Stock Tharamine starts its normal worker entrypoints; supported development flags and network policy restrict integrations. Missing credentials alone does not prevent outbound traffic. Startup may execute application initialization, indexes, TTL behavior and workers; inspect changes to these when selecting a different revision.

Check profile health, then resume laptop sessions and run `chart_sync wait`. Test the frontend through the [daily guide](GETTING-STARTED.md). For incompatible cross-repo changes, stop apps before transfer: synchronization is not an atomic release across repositories.

Rollback uses the previous prepared workspace: stop apps, select that workspace and start again. A synced rollback workspace also requires its valid freeze checkpoint. Never rebind/delete PVCs or regenerate keys to recover a source-selection failure.

## 5. Acceptance and daemon lifecycle

Record the following for each developer separately from fixture results:

1. All three repo fingerprints match after `wait`; synthetic excluded files do not arrive.
2. A controlled change in each actual backend produces a unique behavior/marker without replacing the Pod or image. Restore it from the laptop and verify again.
3. Tailscale disconnect, an offline edit and reconnect recover with matching content. Do not kill a shared daemon merely to test transport recovery.
4. Repeated setup preserves owned IDs and unrelated sync configuration.
5. Frontend sign-in and workspace save/reload work with retained profile data and identities.
6. The profile cannot use another profile's database credentials or cross its network boundary. Full second-profile acceptance requires operator work; Mongo-only isolation does not prove full application isolation.

Store dated results in the installation knowledge base, distinguishing agent-observed and developer-reported evidence. Do not modify real records for a retention test without an agreed fixture.

After reboot or login, connect Tailscale and run `chart_sync status`. Session queries start the daemon when absent unless `MUTAGEN_DISABLE_AUTOSTART=1`; PC/SSH preflight must succeed first. Intentionally frozen sessions stay paused. For active sessions, run `wait`; to resume an intended paused workflow, run `resume`, then `wait`.

On-demand startup is the default operating procedure. Optional macOS login startup uses `mutagen daemon register`. This changes the per-user daemon, affecting every profile/workflow using it. Coordinate before stopping or registering it. Mutagen 0.18.1 requires the daemon stopped for registration changes:

```sh
"$CHART_TOOLS/mutagen-0.18.1/mutagen" daemon stop
"$CHART_TOOLS/mutagen-0.18.1/mutagen" daemon register
"$CHART_TOOLS/mutagen-0.18.1/mutagen" daemon start
```

The launchd plist records the binary's path. Moving to a different versioned path requires coordinated stop/unregister/register/start; registration alone does not replace an existing plist. Update helper/version pins together and inspect the resulting registration. Registration does not resume paused sessions. [Daemon lifecycle](https://mutagen.io/documentation/introduction/daemon/), [pinned macOS implementation](https://github.com/mutagen-io/mutagen/blob/v0.18.1/pkg/daemon/register_darwin.go).

App teardown does not pause source transfer. For runtime suspension, pause owned laptop sessions, stop apps and optionally stop Mongo. Retain sessions, data and identity state; no automatic termination, forced conflict reset or volume deletion is part of this workflow.

## 6. Optional development .env.local import

The developer's agent performs this procedure. There is no importer script or bulk environment replacement command. Frontend env stays on the laptop; this procedure covers backend `auth`, `tharamine` and `orange` settings.

### Review privately

1. Identify the developer/profile, selected workspace, service and exact env-file path. Inspect `identity/apps/inventory.json`, configuration paths and volume identities. This procedure does not provision Linux accounts or an app profile.
2. Ask for the path, never contents in chat. Parse privately with a dotenv-aware parser; pinned Node 24 provides `node:util.parseEnv`. Do not source the file as shell code or split naively at every `=`. Resolve interpolation or dotenvx encryption explicitly; do not assume the parser expands variables or provides keys.
3. Read consumers in the actual checked-out revision. Samples are not exhaustive schemas. Prepare a key-name-only report of settings to import, adapt, retain or omit. The requested import authorizes ordinary development settings; clarify unresolved intent or identity changes without asking approval for every variable.

| Setting | Treatment |
| --- | --- |
| Application preferences/local features | Import reviewed values. Missing keys retain existing values; absence is not a deletion request. |
| Browser origins, URLs and cookies | Adapt to the actual laptop frontend/proxy. Keep backend-to-backend addresses separate. |
| Mongo, Redis and internal service endpoints/credentials | Retain profile-generated values and cluster DNS. Laptop `localhost` points to the Pod after deployment. |
| Signing/session/encryption keys, key paths and service-auth registry | Preserve retained profile identities. Translate paths only to matching mounted development keys. Use `apps prepare-backend-key` for a missing auth backend key. Replacing an identity requires explicit intent and a retained backup. |
| Runtime/listen settings, telemetry, external integrations, proxies and Node loader options | Preserve development/offline restrictions. An env import does not authorize live delivery, capture, metadata/history fetching, production endpoints or network-policy changes. |
| Git/npm credentials | Keep in private fetch/installer configuration, outside runtime mounts. |
| Unknown variables | Read consumers before deciding; do not silently import destinations or runtime behavior. |

### Transfer and apply

4. If needed, transfer through SSH/SCP to a new owner-only directory under `/mnt/hdd/shared-dev/profiles/<profile>/identity/env-imports/<service>/<timestamp>/`. Use directories 0700/files 0600. Retain earlier imports. Keep raw files outside mounted config, source, Git and Obsidian; never put values in command arguments, chat or logs.
5. For an existing profile, freeze laptop sessions and stop apps, keeping Mongo/data running. For initial setup, apply reviewed settings after identities exist and before app startup. Do not fetch code, run migrations or reset data during import.
6. Use registered-host/HDD guards and the app/Mongo lifecycle locks before writing. Back up original configs and record paths, volume UIDs and key fingerprints privately. Merge only reviewed entries into canonical `identity/apps/<service>/config.json` with a JSON-aware writer and atomic replacement at mode 0600. Preserve unrelated keys/files. Release locks before invoking chart commands that acquire them.
7. Do not edit `identity/apps/runtime-configs/` directly. While the matching source checkpoint remains frozen, run `apps select --profile <profile> --workspace <selected-workspace>` to select configuration derived from the canonical files, then `apps up`. A dependency mismatch requires explicit dependency preparation, never a bypass.

### Verify and recover

8. Check readiness/API health and inspect logs privately. Verify effective mounted settings, the requested feature, profile endpoints, data/key retention, source exclusions and external-egress restrictions. For worker/destination changes, run `python3 tests/app_egress.py --profile <profile>` after reviewing its scope. Do not probe live APIs.
9. Resume owned sessions only after startup is healthy, then verify synchronization. Store a values-free receipt with imported/adapted/retained key names, private backup paths and results. Secret values stay in private files.
10. On failure, stop apps, restore canonical config from its backup, select the same workspace and start again. Preserve rejected imports/config generations for inspection. Retain original keys, source, databases and PVCs. Resume synchronization after recovery.

An arbitrary laptop dotenv file is not guaranteed to work unchanged inside a Pod. Configure by observed source behavior and the profile's operating boundaries.

## 7. Operational reference

Use the [operations reference](OPERATIONS.md) for command behavior, runtime prerequisites and storage safeguards. Record installation-specific failures and investigations in [Operational Troubleshooting](/home/sean/obsidian/vault/Chart%20Infra/chart-infra/003%20Operational%20Troubleshooting.md). Keep the acceptance criteria in section 5 separate from dated verification results in the knowledge base.
