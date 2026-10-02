# Seeded images

`image.py` builds a private Ubuntu 26.04 Incus image containing pinned backend
source, Linux dependencies and runtime images. It creates two fresh acceptance
boxes, tests their independent identities and retained synthetic data, and moves
`chart-golden` only when those gates pass and the build requested `--promote`.
Existing boxes and their laptop sessions are unaffected.

Host foundation: [Preparation](README.md). Runtime contract: [Applications](APPS.md).
Laptop handover: [Source synchronization](SOURCE-SYNC.md).

## Inputs and authority

Run source preparation as the developer with GitLab SSH access and permission to
read the host Docker image. Building and verification require the host operator's
sudo and the registered host config. Do not give developers host Incus access.
Run from a clean, committed chart-infra checkout; the builder takes a committed
helper snapshot so edits made during a build cannot change its guest scripts.

- `versions.lock.json` pins the Ubuntu base, primary packages and Tailscale artifact.
  Installed transitive package versions are recorded; APT repositories are not
  frozen snapshots, so repeat builds need not be byte-identical.
- `sources.lock.json` pins three full backend commit IDs. Selection is explicit;
  no pin timer is installed. Preparation fetches those objects, not moving refs.
- The Node image must already be loaded on the host. The default immutable ID
  uses Node 24.20.0, pnpm 11.28.2, amd64 and UID 1000. `--runtime-image` permits an
  explicit loaded image ID; guest verification still enforces those tool versions.
- The host npmrc can contain multiple registry credentials. Only its single
  resolved `//registry.npmjs.org/:_authToken=` entry reaches the builder, over
  stdin into tmpfs. Do not supply tokens on the command line or in Git.
- The SSH argument is a public key for the acceptance boxes. Tailscale enrollment
  is interactive and separate; no enrolled state enters the image.

Source filtering uses `sync_common.py`. Tracked `.env.sample` and the other
recognized templates remain unchanged. Private dotenv files, npmrc, key files,
Git metadata and dependency/output directories are excluded. Template contents
are source examples, not the box's runtime configuration. Build checks scan for
the actual installer token, including nested Docker storage, and reject private
key/token patterns in included non-template source. These checks are not a
complete secret-discovery audit. Publish no image publicly.

## Prepare an immutable input generation

Choose absolute paths outside the checkout. Keep every prior generation.
`prep.py fetch` obtains the checksummed public base/Tailscale artifacts; skip it
when the verified artifacts are already present.

```sh
CHART_HOST_CONFIG=/absolute/private/path/host.json
CHART_ARTIFACTS=/absolute/cache/path/incus-artifacts
CHART_INPUTS=/absolute/cache/path/image-inputs/generation-name
CHART_GIT_CACHE=/absolute/cache/path/image-git
CHART_NPMRC=/absolute/private/path/.npmrc
CHART_PUBLIC_KEY=/absolute/path/operator-key.pub
CHART_BUILD=seed-example

python3 incus/prep.py fetch --artifacts "$CHART_ARTIFACTS"
python3 incus/image.py prepare-inputs --inputs "$CHART_INPUTS" \
  --git-cache "$CHART_GIT_CACHE"
python3 incus/image.py prepare-inputs --inputs "$CHART_INPUTS" \
  --git-cache "$CHART_GIT_CACHE" --apply
```

This explicit networked step writes filtered source archives, a saved runtime
image and `inputs.json` with checksums. It never changes running source or apps.
An existing output directory is refused; use a distinct generation after a failed
preparation. The Git cache is reusable.

## Build a candidate

Review the plan without sudo or `--apply` first:

```sh
python3 incus/image.py image-build --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --inputs "$CHART_INPUTS" \
  --artifacts "$CHART_ARTIFACTS" --npmrc "$CHART_NPMRC" \
  --ssh-key "$CHART_PUBLIC_KEY" --promote

sudo python3 incus/image.py image-build --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --inputs "$CHART_INPUTS" \
  --artifacts "$CHART_ARTIFACTS" --npmrc "$CHART_NPMRC" \
  --ssh-key "$CHART_PUBLIC_KEY" --promote --apply
```

The build:

1. Checks host/HDD identity, owned Incus resources, SSD capacity, source/runtime
   checksums and the clean helper revision. Saves private inputs and metadata
   under `/var/lib/chart-incus/images/<build>/`.
2. Creates `chart-bld-<build>` from the pinned base, without an HDD data device,
   TUN device or developer identity. Installs packages, pulls pinned Mongo and
   Dragonfly images, loads the Node image and installs frozen dependencies.
3. Verifies portable artifact receipts, excludes per-box identities, clears
   disposable builder OS identities/logs and stops Docker and the builder.
   Publishes a local private candidate with no alias.
4. Creates `chart-accept-<build>-a` and `-b` with independent HDD roots, isolated
   UID maps, fresh SSH keys, the guest firewall and host-matched timezone.
   Apps remain unstarted until verification. Host LAN/inotify setup belongs to
   `prep.py`, not the image.

`--promote` records intent; it does **not** move the alias during this command.
The build prints the two Tailscale enrollment commands. Complete both on the
registered tailnet before verification. Tailnet ACLs must permit operator access
to their SSH, Mongo and API ports. Each box has its own tailnet identity.

## Verify and optionally promote

```sh
sudo python3 incus/image.py image-verify --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --apply
```

Verification adopts the preinstalled artifacts without npm credentials, creates
only each acceptance box's `image-fixture` dataset, generates private identities,
and starts the stack. It tests Mongo transactions/change streams and user
permissions, runtime egress denial, API login/workspace save, resource policy,
and retained records/identities across Compose down/up. It compares machine, SSH,
Tailscale, Mongo, database-user and application-signing identities across boxes.
Host probes require Tailscale access and deny bridge access to ports 22, 27017
and 3000; a peer probe checks bridge SSH denial. This is a bounded network check,
not proof of all tailnet ACLs or malicious-root isolation.

On success, apps and backing services are stopped; SSH/Tailscale remain available.
With promotion requested, `chart-golden` points at the tested fingerprint and
`chart-golden-previous` retains the preceding owned target. Previous images,
builder, acceptance boxes, data and input generations remain. Alias movement
never updates an existing box. Without `--promote`, verification records a tested
candidate but does not move either alias.

Private `build.json`, `seed-manifest.json`, package inventory and per-attempt logs
record the result. Guest reports live at
`/srv/chart/data/identity/image-accept.json`. A failed check prevents promotion;
inspect retained state before recovery. Partial creation/builds are not resumable:
choose another build name after diagnosis. A completed guest fixture can be
reused if the host exposure check failed, with its identities retained.

## Boundaries and recovery

This workflow publishes and verifies candidates. General developer creation from
`chart-golden`, adoption of an existing HDD dataset, whole-box backup/restore,
named-dataset switching and the scheduled pin updater require separate work.
`prep.py box-create` still uses the pinned Ubuntu base. Do not recreate a live
box to try the seed or substitute its HDD directory for an acceptance directory.
Actual laptop/browser acceptance and whole-box restart are separate from these
API and Compose checks.

`image_guest.py adopt --box <name>` imports portable receipts only after normal
box/backing registration, into a fresh application state directory. It neither
creates app keys nor starts services. It refuses existing app state; it is not a
retained-data recovery command. Keep the sealed manifest and source fingerprints
intact. Laptop pairing uses the normal explicit bundle-to-mirror handover.

Keep build records private. Never publish an enrolled developer box. Do not run
prune, `down -v`, delete image generations or remove failed boxes/data as cleanup.
Check `sudo python3 incus/prep.py status --config "$CHART_HOST_CONFIG"` before
another build; retained images and dependency generations consume SSD capacity.

Incus's [image publication guide](https://linuxcontainers.org/incus/docs/main/howto/images_create/)
describes stopped-instance publication and identity sanitization.
