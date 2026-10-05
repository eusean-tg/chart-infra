# Developer-agent setup and daily use

A personal box is a Linux development machine. The developer is root inside it;
the operator owns the host, network boundary, OS image and HDD backups. Discover
project setup from each repository's README/agent instructions. Reference examples
below are starting points, not an enforced application contract.

Before setup, inspect existing mappings/sessions and collect unresolved choices
with the developer: checkout paths and wanted exclusions; data source and actual
database names; private npm/env/dev-key sources; existing supervisor or systemd;
and the frontend's backend/proxy settings. Reuse decisions already supplied.
Record these in the developer's private box notes. Keep operator instructions
separate from a laptop-agent-owned results note so concurrent agents do not
rewrite the same handoff while executing it.

## 1. Establish the box and laptop mapping

Get the box name and SSH host-key fingerprint from the operator. Compare the
presented host key before trusting it, then connect with the developer's key:

```sh
ssh root@<box>
```

Do not disable host-key checking. The box is a separate Tailscale node; confirm
`tailscale ping <box>` from the laptop. Operator tasks require host sudo; developer
work requires only box SSH. Guest root has no host Incus or Kubernetes authority.
The guest inherits host timezone (`Asia/Kuala_Lumpur` on Sean's PC).

Obtain the reviewed `skills/chart-box/` directory from chart-infra, including its
scripts and this guide. Keep a local chart-infra checkout or an equivalent bundle
with working relative links. Symlink the skill into the agent's existing skills
directory without replacing an unrelated installation:

```sh
ln -s /absolute/chart-infra/skills/chart-box ~/.codex/skills/chart-box
# For Claude Code, use ~/.claude/skills/chart-box instead.
```

The helper needs Python 3.11+ and Mutagen 0.18.1 on the laptop. Do not replace
unrelated Mutagen sessions or daemon-login preferences. Discover every checkout's
absolute path; do not assume `~/workspace`. Inspect Syncthing and other sync roots.
Discover regular `~/.config/chart-box/*.json` mappings before creating one. Match
their box/repo to the task; resolve ambiguous targets with the developer. Prefer
`box.json` for a first single box; preserve an existing named mapping and pass its
`--config` on every helper call. Config symlinks are refused by the helper.

## 2. Pair repositories

For each Git checkout, choose a unique destination name. Any repository is allowed:

```sh
python3 /absolute/chart-infra/skills/chart-box/scripts/sync.py setup \
  --box <box> --repo orange-v2-backend --source /absolute/laptop/checkout
python3 /absolute/chart-infra/skills/chart-box/scripts/sync.py resume --repo orange-v2-backend
python3 /absolute/chart-infra/skills/chart-box/scripts/sync.py flush --repo orange-v2-backend
```

Setup writes `~/.config/chart-box/box.json`, with the box/SSH target, discovered
laptop paths, `/srv/chart/source/<repo>` destinations and owned session IDs. A
separate `--config` supports another box. New sessions start paused. Setup refuses
source overlap with other Mutagen sessions or discoverable Syncthing folders,
and refuses existing included source at a fresh destination. Sessions from other
workflows require deliberate migration; do not reuse their paths implicitly.
Inspect prior sessions only if present; if already retired, cite their recorded
disposition instead of recreating their mapping or running retirement again.

New ordinary files (for example `src/feature.ts`) sync immediately without
`git add` or a commit. Only the private-pattern exception depends on Git tracking.
Synchronization is one-way-safe. `.git`, node_modules, build output and caches are
excluded. Untracked `.env*`, PEM/key files, `.npmrc` and `.netrc` are excluded;
`.sample`/`.example` templates and tracked files matching private patterns are
included. The helper does not inspect their contents or decide whether the repo
should commit them. Ignored box-side paths are retained. Symlinks are not mirrored.
The helper has no per-repo extra-ignore setting. Inspect its policy before pairing;
record accepted repository noise rather than editing a shared policy mid-session.
Source files were observed as `0600 root` in acceptance. The helper sets no mode
override; choose and verify permissions explicitly before using a non-root runtime.

Tracked exceptions are captured at setup/refresh because Mutagen's ignore policy
is fixed per session. **Pause before changing tracking status or switching branches
that change tracked private-pattern paths.** Run `refresh`, `resume`, then `flush`.
The helper also checks for changes on resume/flush, pauses on mismatch and reports
the required refresh. A continuously running session does not monitor Git's index;
this is not a guarantee against a developer making a tracked file private while
its old exception remains active. Refresh replaces only the owned session record,
retains both trees and lets one-way-safe conflicts stop overwrites.

Always run the helper's `flush` before remote tests/startup depending on edits. It
flushes Mutagen, checks endpoint errors and compares included-file fingerprints.
A raw flush alone does not establish matching content. Keep edits quiescent during
that check. Normal ongoing hot reload requires neither pauses nor activation steps.
[Mutagen ignore behavior](https://mutagen.io/documentation/synchronization/ignores/)

## 3. Match repository tooling

Read `.nvmrc`, `engines`, `packageManager`, lockfiles and repository setup guidance.
Inside the box, load nvm explicitly for noninteractive SSH:

```sh
ssh root@<box> 'bash -lc "cd /srv/chart/source/orange-v2-backend; . /opt/nvm/nvm.sh; nvm install; nvm use; node --version"'
```

`nvm install` without a version requires `.nvmrc`; supply the repository's required
version otherwise. Install pnpm at the repository's selected version using its
recommended method. Corepack availability depends on the selected Node version;
do not assume it is preinstalled. Install dependencies in the box, not on the laptop
for transfer. Native build tools and Python are in the base image.
[Nvm usage](https://github.com/nvm-sh/nvm#usage)

## 4. Private configuration and keys

The developer supplies their own npm registry access. Copy their private npmrc
through SSH/SCP without printing its contents; set mode 0600. No shared operator
npm token is injected by the infrastructure.

Review each project's `.env.local` and key requirements. Copy private configuration
and keys over SCP, adjust Mongo/cache/service URLs for the box topology, and retain
existing private material before replacing it. This is agent-led configuration,
not an env importer or a source patch. Tracked env examples remain unchanged.

Review env settings without printing values. Use the current sample's key layout,
overlay developer settings, then inspect developer-only keys against source,
package scripts, loaders and dynamically constructed names. Classify ambiguous
keys for review rather than deleting them on a failed text search. Apply box URLs,
actual imported database names and key paths; parse using the application's own
loader. Key counts help detect omissions but do not prove valid values. Compare
key names and required settings, then verify startup. Copy only referenced dev
keys; do not copy production-labelled or unrelated credentials automatically.

For nightly HDD coverage, keep private copies under
`/srv/chart/data/private/<repo>/` and link excluded paths such as `.env.local` or
untracked `keys/` into the source directory, if the application supports that.
Alternatively copy them into the mirror and maintain a separate private backup.
Files stored only in `/root` or `/srv/chart/source` are outside nightly HDD backup.
Do not modify a tracked path only on the box; make source changes on the laptop.

## 5. Backing services and application startup

Use the developer's existing Compose project or adapt repository instructions.
Keep durable database bind paths under `/srv/chart/data`; default Docker volumes
live on SSD and are not included in the HDD backup. Developers own Compose and
may run normal lifecycle commands. Check volume bindings before destructive work.

Reference Mongo/Dragonfly shape:

- One Mongo member with `--replSet rs0` is sufficient for ordinary development.
  Use `rs.initiate` for a fresh instance. Combining `--auth` and `--replSet`
  requires a keyfile (including for one member). Set keyfile ownership/mode for
  the selected image; create your own users/passwords. Do not reuse operator keys.
- Retain a `mongo/member-0` directory on HDD if adding members later is useful.
  Select the repository's compatible Mongo version and explicit cache tuning.
- Set Mongo's soft/hard `nofile` ceiling to 64000 and check the running process.
  The inherited 1024 ceiling failed during the chart applications' schema setup.
  This file-descriptor ceiling does not reserve memory or set a CPU/memory limit.
- Dragonfly can use `/data` on HDD with a developer-selected snapshot policy and
  password. Stop/snapshot behavior belongs to that Compose project.
- The accepted unprivileged box rejects unlimited `memlock`; omit that ulimit
  rather than granting host privileges. Dragonfly can fall back from io_uring to
  epoll. Check actual authenticated protocol health as well as container status.
- Publish Docker ports on `127.0.0.1` for box-local access. Use the
  [Tailscale socket example](reference/tailnet-tcp.md) for laptop access without
  requiring the Tailscale address when Docker starts. Docker forwarding does not
  traverse the guest input chain; it is not governed by that chain alone.
  [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)
  Compass URI shape:
  `mongodb://<user>:<password>@<box>:27017/<database>?authSource=admin&directConnection=true`.
  Match `authSource` to where the user was created. Plain TCP over Tailscale needs
  no Mongo CA or resolver configuration.

For a developer-owned Compose stack, a minimal Mongo port/data fragment is:

```yaml
services:
  mongo:
    image: ${MONGO_IMAGE} # choose a repository-compatible version/digest
    command: [mongod, --replSet, rs0, --bind_ip_all, --auth, --keyFile, /run/mongo-keyfile]
    ports: ["127.0.0.1:27017:27017"]
    restart: unless-stopped
    ulimits:
      nofile: {soft: 64000, hard: 64000}
    volumes:
      - /srv/chart/data/my-project/mongo/member-0:/data/db
      - /srv/chart/data/private/mongo-keyfile:/run/mongo-keyfile:ro
```

This fragment requires prepared keyfile/users and replica initialization; it is
not a complete unattended bootstrap. The developer's agent supplies the matching
setup from the selected Mongo image documentation/repository.
For native box-local apps and loopback-published Mongo, a member address of
`127.0.0.1:27017` supports replica-set discovery; laptop clients use
`directConnection=true`. Containerized app clients need a member address reachable
from their own network namespace. Do not copy a topology's address blindly.

Run backends natively in the mirrored checkout with `pnpm install` and its own
`pnpm dev` command. Discover service dependencies/ports. Choose a developer-owned
process supervisor or terminal sessions for persistent processes; record those
commands privately so later agents can find logs and restart the right service.
Do not assume a universal `box up`, service unit or Compose service name.
For boot/crash recovery, systemd is available in the bare box; use the
[native-app unit example](reference/native-app.service). Compose services can use
`restart: unless-stopped`; deliberately stopped services stay stopped. Neither a
restart policy nor unit ordering proves database readiness. Verify the real
restart path and application retry behavior. Retain copies of developer units
under `/srv/chart/data/private/systemd/` for HDD backup; `/etc/systemd/system`
alone is outside its scope.

Boxes have normal Internet egress and developer-owned keys. Live integration work
is the developer's responsibility, as on their laptop. The shared k3s restrictions
remain separate. Host private-network restrictions and tailnet ACLs still apply.

## 6. Import an existing Mongo dump

The agent inspects the dump format, source Mongo version/FCV, target version and
application database names. Preserve the original archive; copy it over SCP and
verify checksums. Use `mongorestore --archive=<path> --gzip` for a gzip archive,
with the developer's target URI supplied privately. Select application namespaces
with `--nsInclude`; add reviewed `--nsFrom`/`--nsTo` mappings when needed.
Choose the data source with the developer before arranging an export. Match each
app's database setting to the actual source namespace; a wrong name can silently
create an empty database. `mongodump` has no `--nsInclude`: use `--db` for a single
database or filter a full-instance archive during restore. Full-instance exports
can contain source authentication data; keep the archive private.
[MongoDB dump options](https://www.mongodb.com/docs/database-tools/mongodump/)

Prefer an empty application database/data directory for first import. Restoring
into existing data or using `--drop` needs an explicit decision about overwriting
that developer's data. Do not import Mongo `local` replica metadata or source
users from `admin` unintentionally. Keep the target replica identity and chosen
credentials. Check restore errors, collection/index counts and actual login/save
behavior; TTL data can expire after startup. This procedure does not require an
infrastructure dataset registry or adoption CLI.
[MongoDB restore reference](https://www.mongodb.com/docs/database-tools/mongorestore/)

## 7. Daily use and recovery

1. Edit/pull on the laptop. Check whether a branch switch needs policy refresh.
2. Flush the relevant session, then run tests or inspect the backend over SSH.
3. Reinstall dependencies in the box after dependency changes, following the repo.
4. Run Vite locally with its API proxy aimed at `http://<box>:3000` (or the chosen
   backend port). Plain HTTP over Tailscale is the development default; HTTPS is separate.
   For kiyotaka-frontend, inspect `VITE_BACKEND_DOMAIN` and
   `VITE_CME_SNAPSHOT_PROXY_TARGET` in its current configuration. Preserve old values
   before switching. General Tailscale HTTPS needs the tailnet admin's setting.
5. Verify hot reload, login (including the repository's dev email-code flow), and
   workspace save/reload. Those are application checks performed by the developer.
   Sign in using an identity from the chosen data source; pilot fixture users are
   not expected in a developer's own dump.

For restart acceptance, coordinate a brief interruption and run `systemctl reboot`
inside the developer's own box. Verify a changed boot ID, SSH, service readiness,
sync and saved data afterwards. Do not restart the shared host. Operator recovery
is needed if the box cannot return. Keep the laptop results in their separate
results note; fold accepted findings into the maintained guides and vault evidence.

If an edit is missing, check sync status, pause/connectivity/errors, then flush and
compare content before blaming a watcher. Preserve conflicts; never switch to
one-way-replica to erase a remote edit. Use the recorded supervisor for logs/restarts.
If the box is stopped or host storage is unavailable, contact the operator.

Recreation is for OS/image changes. The operator preserves HDD data and enrolled
identity, and retains the prior SSD rootfs/export. The replacement has empty source
and no installed project dependencies. Coordinate paused sessions, let the agent
reconcile/resume them, and reinstall project tooling/configuration. Ordinary
repository updates do not require recreation.
