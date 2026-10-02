# Box applications

Legacy reference for Sean's existing managed pilot. Personal-box onboarding uses
[the developer guide](DEVELOPER.md); do not install this managed application
contract into a bare box. Preserve the running pilot until explicit migration.

`box.py` prepares and operates auth, Tharamine and Orange inside an enrolled
Incus box. Run it as box root over verified SSH. Read [preparation](README.md)
and [backing services](BACKING.md) first. These commands do not operate k3s.

Source staging, dependency installation, identity preparation, workspace selection
and startup are explicit operations. Startup downloads nothing. App containers
use UID 1000, read-only source/dependency/config mounts, an internal-only Docker
network and no CPU/memory reservations or caps. Orange's port 3000 is available
through an on-demand systemd socket bound to the box Tailscale address and
`tailscale0`. Auth, Tharamine and Dragonfly have no laptop listeners.

## Install helpers and toolchain

Install matching reviewed copies of `backing.py`, `box.py`, `box_config.py`,
`app_checks.py`, `backing_checks.py`, `box_sync.py` and `sync_policy.py` under `/opt/chart-infra/incus/`.
Install the repository's `sync_common.py` and `runtime/launch.cjs` there too.
Retain the installed revision before upgrading helpers. Do not overwrite a
foreign Compose definition, identity receipt or proxy unit to bypass a guard.

Build the repository's `runtime/Dockerfile` during explicit package preparation,
or transfer an already verified image using `docker image save` and `load`.
The runtime is Node 24.20.0 with pnpm 11.28.2, UID/GID 1000. Register the actual
immutable image ID; a host-local registry address is not a box registry.

Inside the box:

```sh
CHART_BOX=$(hostname)
CHART_APPS=/opt/chart-infra/incus/box.py
python3 "$CHART_APPS" runtime --box "$CHART_BOX" \
  --image sha256:<verified-loaded-image-id> --apply
python3 "$CHART_APPS" identity --box "$CHART_BOX" --apply
```

Identity preparation consumes the existing backing-service users/cache password.
It generates independent app signing/session identities only once. Missing or
changed retained identity files fail verification; they are not regenerated.

Mongo needs the explicit `nofile=64000` definition in `backing.py`. An inherited
1024-file ceiling can exhaust descriptors during app schema initialization even
when the small backing fixture passes. This raises file capacity without
reserving CPU or memory. Existing deployments with the earlier definition need
a stopped, reviewed definition update with retained configuration/data backup.

## Prepare a source workspace

Discover the developer's source locations and selected commits. Fetch source
separately on an authenticated machine. Do not copy the PC's dirty worktree,
private config or Git credentials into a box. A stock baseline uses one tar
archive per recorded 40-character commit. Filter it using `sync_common.ignored`
before transport so excluded secrets never enter the box artifact cache; record
the resulting SHA-256. `source` also applies that policy, rejects included
symlinks/devices/path traversal and records included-file fingerprints.

Transfer reviewed bundles into `/srv/chart/cache/artifacts/` and run for each
service (`auth`, `tharamine`, `orange`):

```sh
python3 "$CHART_APPS" source --box "$CHART_BOX" --workspace baseline \
  --service auth --archive /srv/chart/cache/artifacts/auth.filtered.tar \
  --sha256 <bundle-sha256> --revision <recorded-commit> --apply
```

Source lives at `/srv/chart/source/<workspace>/<repo>` on SSD. Source files use
0644, executable files/directories 0755. Bundle workspaces are immutable: use a
different named workspace for a different baseline. Neither selection nor
teardown deletes prior workspaces or modifies a laptop checkout.

Use [Laptop source synchronization](SOURCE-SYNC.md) to retain a bundle and
hand its path over to a verified laptop mirror. Do not aim a raw session at a
bundle or use the k3s controller as a box controller. Mixed bundle/mirror source
is supported; live Mac save/rename/delete/reconnect acceptance is separate.

## Prepare dependencies

The agent selects the effective npmjs registry credential from the authorized
local npm configuration and transfers only that registry line through private
SSH stdin. Store it at `/srv/chart/data/identity/npmrc`, root-owned mode 0600:

```text
//registry.npmjs.org/:_authToken=<private-token>
```

Do not print the token, put it in command arguments or copy all registries from
the laptop. A differing existing credential requires deliberate review. The
developer has root inside their box and can read this shared token; its scope
and expiry require verification in registry settings.

```sh
python3 "$CHART_APPS" deps --box "$CHART_BOX" --workspace baseline \
  --service auth --apply
python3 "$CHART_APPS" deps --box "$CHART_BOX" --workspace baseline \
  --service tharamine --apply
python3 "$CHART_APPS" deps --box "$CHART_BOX" --workspace baseline \
  --service orange --apply
```

Installers run separately with registry access on `chart-preparation`, a private
tmpfs credential projection, writable dependency volume and independent per-box
pnpm store. Runtime containers receive none of those credentials or the
preparation network. Install logs are private and redact the selected token.
The shared store is `/srv/chart/cache/pnpm`; dependency volumes are on SSD under
Docker's data directory. The dependency key includes the immutable image ID and
package/lock/workspace/hook inputs. Installation verifies source before and after.

Failed volumes remain available. Inspect the failure before choosing another
`--attempt <name>`; never prune to force a retry. Matching completed generations
are reused. Source/dependency preparation refuses at 85% guest filesystem usage
and reports the 70% review threshold. The operator also checks host SSD reserve
and btrfs allocation using `prep.py status`; guest free-space reporting cannot
replace those host checks. There is no automatic cleanup.

## Select and use

```sh
python3 "$CHART_APPS" select --box "$CHART_BOX" --workspace baseline --apply
python3 "$CHART_APPS" up --box "$CHART_BOX" --apply
python3 "$CHART_APPS" status --box "$CHART_BOX"
curl --fail "http://$(tailscale ip -4):3000/api/v1/health/services"
python3 "$CHART_APPS" stop --box "$CHART_BOX" --apply
```

Selection requires stopped apps and matching source, dependencies and identity
receipts. `up` requires healthy backing services and waits for auth, Tharamine
and Orange before opening the API listener. The stock applications run their
own startup schema/index logic; use a compatible development dataset and retain
a backup before changing app revisions. The helper does not implement a generic
database migration or archive importer.

`stop` closes the API listener and stops apps. `down` also removes app containers,
while preserving external dependency volumes, source, config and all databases.
Backings refuse lifecycle changes with running registered app writers. For full
Compose teardown and recreation:

```sh
python3 "$CHART_APPS" down --box "$CHART_BOX" --apply
python3 /opt/chart-infra/incus/backing.py down --box "$CHART_BOX" --apply
python3 /opt/chart-infra/incus/backing.py up --box "$CHART_BOX" --apply
python3 "$CHART_APPS" up --box "$CHART_BOX" --apply
```

Backing teardown saves Dragonfly before stopping it. Do not substitute `down -v`,
prune, unguarded dataset removal or a second database stack over the same files.
Named-dataset switching, whole-box recovery and sanitized golden-image publication
remain separate deliverables.

## Synthetic and browser acceptance

```sh
python3 /opt/chart-infra/incus/app_checks.py --box "$CHART_BOX" --apply
# After the teardown/recreation sequence:
python3 /opt/chart-infra/incus/app_checks.py --box "$CHART_BOX" \
  --expect-existing --apply
```

The check creates retained synthetic `guest`/`frontend_dev` roles, the user
`box-browser@chart.test`, and one workspace. It refuses colliding role/user
identities. It exercises the API login-code flow, workspace persistence,
read-only mounts, resource settings and prompt denial to controlled listeners
on the box Tailscale address and Docker gateway. No vendor API is a test target.
Repeat login requests respect the app's per-address cooldown; do not reset its
rate-limit data. Reports under `identity/apps/verification` contain no session
tokens. These API checks do not establish laptop browser or WebSocket acceptance.

For the Mac browser, point the frontend API proxy at `http://<box>:3000` and
use the synthetic email. Normal Vite development mode is supported; the offline
wrapper is optional and browser-side vendor blocking is not an acceptance gate.
Obtain the recent code over box SSH:

```sh
python3 /opt/chart-infra/incus/box.py login-code --box "$(hostname)" \
  --email box-browser@chart.test
```

Verify sign-in, authenticated navigation, workspace save/reload and persistence
after app restart and Compose recreation. Keep automated verification on
synthetic fixtures; do not invoke live APIs as test targets. Optional integrations
need their own configuration; a healthy login stack does not prove every app feature.

## Agent-reviewed development environment imports

Discover the requested `.env.local` privately; do not sync it or create an import
script. While apps are stopped, retain a mode-0600 copy of the selected config
and `identity.json` in an explicitly named private backup directory. Review only
the requested development settings against the selected stock source. Preserve
box Mongo/cache/service endpoints, generated signing identities and offline
restrictions. Never copy production credentials or replace config wholesale.

Apply reviewed changes to `identity/apps/<service>/config.json` as UID/GID 1000,
mode 0600. Preserve every key file. Verify unchanged-file hashes against the old
receipt; update only the reviewed config's hash in `identity.json`, keeping the
prior receipt. Start through the helper and prove the requested feature. Record
setting names and verification, never values or raw private diffs. This manual
receipt update is an explicit reviewed configuration generation, not permission
to discard identity checks during troubleshooting.
