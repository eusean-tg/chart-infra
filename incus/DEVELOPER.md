# Developer-agent setup and daily use

A personal box is a Linux development machine. The developer is root inside it;
the operator owns the host, network boundary, OS image and HDD backups. Discover
project setup from each repository's README/agent instructions. Reference examples
below are starting points, not an enforced application contract.

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
and refuses existing included source at a fresh destination. Existing pilot
sessions require a deliberate migration; do not point this helper at their paths.

New ordinary files (for example `src/feature.ts`) sync immediately without
`git add` or a commit. Only the private-pattern exception depends on Git tracking.
Synchronization is one-way-safe. `.git`, node_modules, build output and caches are
excluded. Untracked `.env*`, PEM/key files, `.npmrc` and `.netrc` are excluded;
`.sample`/`.example` templates and tracked files matching private patterns are
included. The helper does not inspect their contents or decide whether the repo
should commit them. Ignored box-side paths are retained. Symlinks are not mirrored.

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
- Bind ports to `127.0.0.1` for box-local access or the box's Tailscale IP for
  Compass/laptop access. Compass URI shape:
  `mongodb://<user>:<password>@<box>:27017/<database>?authSource=admin&directConnection=true`.
  Match `authSource` to where the user was created. Plain TCP over Tailscale needs
  no Mongo CA or resolver configuration.

The managed pilot's actual Compose construction and socket forwarding patterns
remain in [backing.py](backing.py) (`compose_spec`, `proxy`) and
[box.py](box.py) (`proxy`). They are optional reference code; do not install their
inventory/dependency/identity enforcement as personal-box infrastructure.
For a developer-owned Compose stack, a minimal Mongo port/data fragment is:

```yaml
services:
  mongo:
    image: ${MONGO_IMAGE} # choose a repository-compatible version/digest
    command: [mongod, --replSet, rs0, --bind_ip_all, --auth, --keyFile, /run/mongo-keyfile]
    ports: ["${BOX_TAILSCALE_IP}:27017:27017"]
    ulimits:
      nofile: {soft: 64000, hard: 64000}
    volumes:
      - /srv/chart/data/my-project/mongo/member-0:/data/db
      - /srv/chart/data/private/mongo-keyfile:/run/mongo-keyfile:ro
```

This fragment requires prepared keyfile/users and replica initialization; it is
not a complete unattended bootstrap. The developer's agent supplies the matching
setup from the selected Mongo image documentation/repository.

Run backends natively in the mirrored checkout with `pnpm install` and its own
`pnpm dev` command. Discover service dependencies/ports. Choose a developer-owned
process supervisor or terminal sessions for persistent processes; record those
commands privately so later agents can find logs and restart the right service.
Do not assume a universal `box up`, service unit or Compose service name.

Boxes have normal Internet egress and developer-owned keys. Live integration work
is the developer's responsibility, as on their laptop. The shared k3s restrictions
remain separate. Host private-network restrictions and tailnet ACLs still apply.

## 6. Import an existing Mongo dump

The agent inspects the dump format, source Mongo version/FCV, target version and
application database names. Preserve the original archive; copy it over SCP and
verify checksums. Use `mongorestore --archive=<path> --gzip` for a gzip archive,
with the developer's target URI supplied privately. Select application namespaces
with `--nsInclude`; add reviewed `--nsFrom`/`--nsTo` mappings when needed.

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
   backend port). Plain HTTP over Tailscale is the pilot default; HTTPS is separate.
5. Verify hot reload, login (including the repository's dev email-code flow), and
   workspace save/reload. Those are application checks performed by the developer.

If an edit is missing, check sync status, pause/connectivity/errors, then flush and
compare content before blaming a watcher. Preserve conflicts; never switch to
one-way-replica to erase a remote edit. Use the recorded supervisor for logs/restarts.
If the box is stopped or host storage is unavailable, contact the operator.

Recreation is for OS/image changes. The operator preserves HDD data and enrolled
identity, and retains the prior SSD rootfs/export. The replacement has empty source
and no installed project dependencies. Coordinate paused sessions, let the agent
reconcile/resume them, and reinstall project tooling/configuration. Ordinary
repository updates do not require recreation.
