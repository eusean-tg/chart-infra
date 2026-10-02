# Laptop source synchronization

Legacy reference for Sean's existing managed pilot. Personal-box onboarding uses
[the developer guide](DEVELOPER.md); do not install this managed application
contract into a bare box. Preserve the running pilot until explicit migration.

Use `incus/laptop_sync.py` on the developer's laptop and `incus/box_sync.py`
inside their prepared box. Mutagen **0.18.1** transfers one repo per recorded
session, laptop to box in one-way-safe mode. Each repo can use a mirror while
other repos retain their immutable bundles. This workflow does not operate k3s.

Read [Box applications](APPS.md) first. The box must have registered bundles and
prepared dependencies/configuration. Golden-image artifact adoption is separate
work; the handover accepts the registered bundles prepared by `box.py source`.

## Discover and prepare

The agent discovers the box name, verified `root@SSH-host`, workspace, requested
repos and absolute laptop checkout paths. Inspect Git revisions/dirty state and
existing Mutagen/Syncthing roots. Preserve edits and other workflows. Do not
assume a laptop workspace convention or overwrite existing SSH configuration.
Sessions using another SSH alias for the same box need manual overlap review.

Install matching `box.py`, `box_sync.py`, `sync_policy.py`, `sync_common.py` and
application helper dependencies in `/opt/chart-infra/incus/`. Retain prior helpers.
Copy `laptop_sync.py`, `sync_policy.py` and `sync_common.py` together into a
separate laptop tools directory. Reuse the verified Mutagen binary/agent bundle;
no daemon registration, account creation or box listener is needed.
`sync_policy.py` prefers the sibling `sync_common.py`; the repository layout uses
the parent copy only when no sibling exists. Copy the three helper files together
when upgrading. A packaging-only update with an unchanged policy hash requires
no session recreation or new freeze checkpoint.

For a selected workspace, coordinate an app stop before handover. Mongo/cache
remain running. Inside the box, using actual values:

```sh
CHART_BOX=$(hostname)
CHART_WORKSPACE=baseline
CHART_HELPERS=/opt/chart-infra/incus
python3 "$CHART_HELPERS/box.py" stop --box "$CHART_BOX" --apply
python3 "$CHART_HELPERS/box_sync.py" prepare --box "$CHART_BOX" \
  --workspace "$CHART_WORKSPACE" --service auth
python3 "$CHART_HELPERS/box_sync.py" prepare --box "$CHART_BOX" \
  --workspace "$CHART_WORKSPACE" --service auth --apply
```

Repeat preparation only for the requested services: `auth`, `tharamine`, `orange`.
An inactive workspace can be prepared while apps using another workspace run.
Preparation rejects running app containers mounted from the target workspace.

Preparation verifies the bundle fingerprint and retains the original directory
at `/srv/chart/source/.retained/<workspace>/<service>/<registration>`. It creates
an empty mirror at the original `/srv/chart/source/<workspace>/<repo>` path and
keeps `node_modules` as an excluded mount point. No source is fetched, dependency
installed, application started or retained generation deleted. A private HDD
journal records the original directory identity and interrupted-handover state.

## Pair from the laptop

Define this function using discovered values. `CHART_BOX_TOOLS` is a directory
containing the three matching Python helper files; it is independent of k3s tools.

```sh
chart_box_sync() {
  python3 "$CHART_BOX_TOOLS/laptop_sync.py" "$@" \
    --host "$CHART_BOX_SSH" --box "$CHART_BOX" \
    --workspace "$CHART_WORKSPACE" --mutagen "$CHART_MUTAGEN"
}
chart_box_sync plan --service auth --source /absolute/path/auth-service-backend
chart_box_sync setup --service auth --source /absolute/path/auth-service-backend
chart_box_sync resume --service auth
chart_box_sync freeze --service auth
```

`setup` creates a paused session and registers its exact ID and endpoints. It can
recover interruption after session creation and is idempotent for the same
mapping. A recorded missing session is an error, not permission to recreate it.
Laptop state lives in
`~/.local/state/chart-infra/mutagen-box/<box>/<workspace>/<service>/session.json`.
Retired k3s files under `mutagen/<profile>` are not consulted or rewritten.

Each requested repo needs its own `plan`, `setup`, `resume`, `freeze`. Freeze
flushes transfers, verifies clean connected state, pauses the owned session and
compares laptop/box fingerprints. Ordinary pause is not an activation checkpoint.
Stop editing during freeze, dependency installation and selection. The box trusts
its root owner's laptop attestation; this protocol coordinates work, not mutually
untrusted developers inside one box.

The box policy uses 0644 files and 0755 directories, preserves executability and
excludes Git, dependency/build output, dotenv secrets, npm credentials and private
keys. Included symlinks, case collisions and sync-conflict files fail manifest
checks. Preserve source/config separation and the agent-reviewed env-import
procedure. Policy details are in `sync_policy.py` and `sync_common.py`.

## Activate while frozen

Inside the box, run dependency preparation for each paired service while apps
are stopped. Matching completed generations are reused without reading npm
credentials or contacting a registry:

```sh
python3 "$CHART_HELPERS/box.py" deps --box "$CHART_BOX" \
  --workspace "$CHART_WORKSPACE" --service auth --attempt laptop-first --apply
python3 "$CHART_HELPERS/box.py" select --box "$CHART_BOX" \
  --workspace "$CHART_WORKSPACE" --apply
python3 "$CHART_HELPERS/box.py" up --box "$CHART_BOX" --apply
```

Selection requires valid frozen checkpoints for every mirror in that workspace;
untouched bundles keep their existing source/dependencies. The dependency key
includes runtime image ID, `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`
and `.pnpmfile.cjs`. Changed inputs require explicit installation and the private
npm config from APPS.md. Failed volumes, old receipts and old volumes are retained;
choose a fresh `--attempt` after inspecting a failure.

Selection adds the mirror generation to the affected service's Compose labels.
`up` therefore recreates affected containers to mount the replacement directory;
it waits for health and updates the Orange forwarder's container address. It
never installs dependencies or resets a database. App startup may execute the
selected source's own schema/index initialization; use compatible development
source/data and retain backups before changing revisions.

Once health passes, resume each paired session on the laptop and verify transfer:

```sh
chart_box_sync resume --service auth
chart_box_sync wait --service auth
chart_box_sync status --service auth
```

## Daily use and changes

Source edits hot-reload through the laptop-owned mirror. `wait` verifies content
without pausing. `resume` invalidates a frozen checkpoint before any transfer.
Do not edit the box mirror, reset a session or invoke raw Mutagen resume to bypass
the helper. Incompatible cross-repo edits are not an atomic release: stop apps
before transferring them.

For dependency changes, stop apps, finish laptop edits, resume/freeze every paired
repo, prepare changed dependency generations, select, start and resume. Startup
refuses mismatched inputs; it does not stop an already-running watcher when a
laptop edits a package manifest. The agent must coordinate that stop.

Stopping/downing applications preserves mirrors, sessions, dependencies, signing
identities and databases. It does not pause laptop synchronization. After a laptop
restart, use `status`, then `wait` for active sessions or deliberate `resume` for
paused ones. Daemon registration or restart affects all sessions on that daemon
and requires coordination; the helper does neither.

## Recovery and verification

- An interrupted `prepare` with journal phase `intent` blocks source activation.
  Stop relevant writers and rerun the identical command. It verifies the retained
  original inode/fingerprint and accepts only an empty replacement path. Unexpected
  content or replacement identities require inspection; nothing is removed.
- Interrupted setup: rerun the same laptop command with the saved state. Do not
  discard the owner record, select similarly named sessions or reset endpoints.
- Failed freeze: preserve both trees, inspect reported conflicts/extra files and
  resume/freeze after resolving the cause deliberately. Plain pause cannot fix it.
- Failed installation: inspect the private redacted log, retain the failed volume,
  and use a new attempt. Keep sessions frozen until selection/startup succeeds.
- The original source is retained for recovery, but there is no automated
  mirror-to-bundle rollback command. Do not move it over an active session. A
  rollback needs coordinated session retirement, stopped apps, preserved mirror
  contents and an explicit source/receipt restoration plan.

Prove initial equality/exclusions, idempotent setup, an edit in each actual backend,
read-only UID-1000 watcher behavior, rename/delete and disconnect/reconnect.
Record container identity during hot reload and after first activation. Verify
login and workspace persistence using a distinct browser fixture. Browser-side
vendor blocking is optional; it is not an Incus acceptance requirement. Backend
networking and disabled collector defaults remain in force.

Offline contracts: `python3 tests/incus_sync.py`. The opt-in
`tests/incus_sync_live.py --help` describes a synthetic real-SSH fixture. It uses
an isolated local daemon and a networkless Docker watcher, retains fixture source,
state and the stopped watcher, and does not select an application workspace. Its
result does not establish Mac or actual backend hot-reload acceptance.
