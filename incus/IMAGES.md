# Bare images, personal boxes and backups

The image contains Ubuntu 26.04, Docker/Compose, SSH, unenrolled Tailscale, the
guest input firewall, native build tools, Python, Git, curl, jq and nvm 0.40.3.
There is no Node version selected, repository content, service image, app CLI,
application configuration or registry token. Developers configure their boxes
using [the developer guide](DEVELOPER.md) and their repositories' instructions.

The host foundation is in [README](README.md). Commands below require operator
sudo for mutations. They do not update existing boxes or laptop sessions.

Image preparation retains rsyslog with a `NonBlocking=yes` service drop-in at
`/etc/systemd/system/rsyslog.service.d/chart-nonblocking.conf`. The builder's
rsyslog is terminated through a validated host PID descriptor and started again
before package operations. Preparation verifies both syslog readers have
nonblocking descriptors and checks log delivery; evidence is `syslog.json` in
the build directory. This mitigates the systemd socket-flush shutdown hang while
preserving `/var/log/syslog`. It does not change host AppArmor policy.
Images published before this preparation step require separate mitigation or
replacement; editing the builder does not modify an existing image.

## Build and accept a generic image

Use a clean committed checkout. Set explicit absolute paths outside it:

```sh
CHART_HOST_CONFIG=/absolute/private/path/host.json
CHART_ARTIFACTS=/absolute/cache/path/incus-artifacts
CHART_NVM_ARTIFACTS=/absolute/cache/path/bare-artifacts
CHART_OPERATOR_KEY=/absolute/path/operator-public-key.pub
CHART_BUILD=bare-example

python3 incus/prep.py fetch --artifacts "$CHART_ARTIFACTS"
python3 incus/image.py fetch --nvm-artifacts "$CHART_NVM_ARTIFACTS"
python3 incus/image.py image-build --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --artifacts "$CHART_ARTIFACTS" \
  --nvm-artifacts "$CHART_NVM_ARTIFACTS" --ssh-key "$CHART_OPERATOR_KEY"
sudo python3 incus/image.py image-build --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --artifacts "$CHART_ARTIFACTS" \
  --nvm-artifacts "$CHART_NVM_ARTIFACTS" --ssh-key "$CHART_OPERATOR_KEY" --apply
```

`versions.lock.json` pins the base, Docker/Compose, SSH, networking and Tailscale.
`bare.lock.json` pins generic build tools and checksummed nvm scripts. These are
infrastructure versions, not backend source pins. Transitive packages are recorded
in the build inventory; APT repositories are not frozen snapshots.

The builder has no HDD attachment, developer identity or TUN device. Its package
setup masks SSH/Tailscale, installs the guest firewall, verifies empty application
source and Docker stores, then sanitizes OS identities before stopped-instance
publication. Images are local/private. Build inputs, package versions, helper
revision and candidate fingerprint stay under `/var/lib/chart-incus/bare-images/`.
No default alias or automatic update mechanism is used.

Two test boxes receive separate HDD directories, isolated UID maps, fresh SSH
host keys and the operator's public login key. The build prints their enrollment
commands. Enroll both into the existing tailnet, then run:

```sh
sudo python3 incus/image.py image-verify --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --apply
```

Acceptance checks nvm/tool availability, an offline scratch Docker build/run,
empty source and service-image stores before that test, active guest firewall,
Tailscale SSH reachability, bridge SSH denial and distinct machine/SSH/Tailscale
identities. It records a verified fingerprint. Test boxes and fixtures remain.
Application tests, repository secret scanning and seeded-source promotion are not
part of image acceptance. A failed check records no verified image; inspect the
retained build.

Guest provisioning waits up to 90 seconds for systemd and D-Bus. Incus can report
a started container before those services can handle `timedatectl` or `systemctl`.
If publication succeeded but test-box provisioning stopped, resume that build:

```sh
sudo python3 incus/image.py image-resume --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --apply
```

Resume validates the recorded candidate, instance ownership and original script
snapshot. It provisions an existing test box only if its HDD identity directory
is absent, preserves completed identities, and creates missing test boxes. A
partial identity, changed image or changed script snapshot requires inspection;
the command does not erase or regenerate it. No image is rebuilt or accepted by
resume. It prints the enrollment commands; use `image-verify` after enrollment.
Failures before publication require diagnosis and a separate build name.

## Create a developer box

Use the verified fingerprint from `image-verify`, not a moving alias:

```sh
sudo python3 incus/boxes.py create --config "$CHART_HOST_CONFIG" \
  --box alex-dev --image <verified-fingerprint> \
  --ssh-key /absolute/path/alex-public-key.pub --apply
```

The command creates/provisions an unprivileged box, attaches its required HDD
root at `/srv/chart/data`, and inherits the host timezone. Creation refuses any
existing instance or retained HDD directory. No application is installed or
started. Enroll the box using the printed command and the developer's own account
in the team tailnet. No auth key belongs in the script or repository.

Repeat `--ssh-key /absolute/path/device.pub` for each authorized laptop/operator
device. Each file must contain one OpenSSH public key; duplicate keys are removed.
Only public keys are copied. Recreation retains the box's authorized-key set.

Give the laptop agent the box name, SSH host-key fingerprint and developer guide.
Verify both operator/laptop `tailscale ping` and SSH; confirm intended teammate
API access separately against tailnet ACLs. Host Incus access stays with operators.

## Nightly HDD backups to NVMe

Policy: **04:00 Asia/Kuala_Lumpur, seven days of completed nightly copies**. A backup
stops one box gracefully, copies its HDD directory, then restores its prior box
running/stopped state. There is no forced stop. Apps return only according to the
developer's startup configuration; a process launched manually does not restart
because the box does. Failed copies retain partial artifacts and attempt to restart
an originally running box. Failure is visible in the systemd service journal.

Backups live under `/var/backups/chart-incus/boxes/<UTC timestamp>/<box>/` on the
host root NVMe filesystem, separate from the HDD source. An off-machine copy is
needed for loss of the whole PC. SSD source,
node_modules, `/root/.npmrc` and arbitrary files outside `/srv/chart/data` are not
included in nightly copies. Place irreplaceable private config under HDD or export
it separately. A stopped copy prevents concurrent guest writes; applications must
still support recovery from their shutdown state. Developers can keep logical
database dumps under `/srv/chart/data/backups/` for application-level restores.

Destination checks require the host root filesystem and a device distinct from
the HDD. Copies preserve 25% free space plus a 1 GiB margin. The data archive uses
an apparent-size estimate; each archive writer receives a file-size ceiling based
on available space. Scratch restore checks the uncompressed archive size. These
checks do not reserve space against concurrent workstation writes. Capacity/copy
failures retain incomplete artifacts; an ordinary backup restarts a box it stopped.
Monitor both filesystem usage and retained manual exports/scratch directories.

Prove a manual copy and restore on a test box before enabling the timer:

```sh
sudo python3 incus/backup.py backup --config "$CHART_HOST_CONFIG" \
  --box <test-box> --apply
sudo python3 incus/backup.py restore-check --config "$CHART_HOST_CONFIG" \
  --backup /var/backups/chart-incus/boxes/<stamp>/<test-box> \
  --scratch /var/backups/chart-incus/restore-checks/<unique-name> --apply
sudo python3 incus/backup.py enroll --config "$CHART_HOST_CONFIG" \
  --box <accepted-personal-box> --apply
sudo python3 incus/backup.py install-timer --config "$CHART_HOST_CONFIG" --apply
```

`restore-check` verifies the archive checksum, extracts into a new scratch directory
and compares file contents/links. It preserves numeric ownership, rejects escaping
links/devices, starts no service and never overwrites a live dataset. Archives
retain GNU tar ACL/xattr metadata; this scratch check validates content/links, not
a full ACL/xattr round trip. Inspect filesystem-specific restore needs before live
recovery. An archive with an external symlink is retained but needs a separately
reviewed restore method; automatic scratch extraction refuses it.

The timer operates only on explicitly enrolled personal boxes with their expected
HDD devices, markers and `user.chart-box=chart-bare-v1`. Enrollment lives in
`/var/lib/chart-incus/backup-enrollment.json`. No box is enrolled by creation.
`sean-dev-pilot`, `chart-test-*`, `chart-bare-*` and `retained-*` cannot enroll;
manual backup remains available. `unenroll --box <name> --apply` removes a box
from the schedule without stopping it or removing copies. Installation requires
at least one enrolled box. The timer runs from a root-owned tool snapshot.
Missed schedules do not trigger daytime catch-up (`Persistent=false`). Pruning
covers only completed nightly HDD-only generations older than seven days with a
newer completed copy. Copies in the former HDD backup directory remain untouched.
Manual backups, rootfs exports and incomplete generations
are retained. A failed nightly run skips pruning. Monitor HDD/SSD space and inspect
`systemctl status chart-box-backup.timer` and `journalctl -u chart-box-backup.service`.

## Test recovery before migration

An opt-in host fixture reuses test box A from a verified image build:

```sh
python3 tests/bare_recovery_live.py --config "$CHART_HOST_CONFIG" --build "$CHART_BUILD"
sudo python3 tests/bare_recovery_live.py --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --apply
```

It creates unique synthetic root/UID-1000 files on that box's HDD, runs recreation
with NVMe backup/rootfs export/scratch restore, and checks file hashes, guest
ownership/modes, SSH identity and Tailscale node identity. It enrolls no additional
Tailscale device. Evidence is `/var/lib/chart-incus/recovery-proofs/<build>/proof.json`.
The previous rootfs remains stopped without its HDD attachment. A partial proof
requires inspection of that receipt and the recreation phase; do not rerun blindly.

After acceptance and before handing a box to a developer, retire disposable image
test instances and remove their corresponding Tailscale device registrations using
an authorized account. Stopping a box alone does not remove its registration.
Preserve HDD directories, archives and evidence unless their deletion is explicitly
authorized. When replacing a working environment, preserve it until the replacement's
application, sync and data acceptance pass. No cleanup is performed by this fixture.

## Published artifact, network boundaries and test retirement

After recovery passes, use the retained pair for final image/network checks:

```sh
python3 tests/bare_boundary_live.py --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --retire-tests
sudo python3 tests/bare_boundary_live.py --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --retire-tests --apply
```

The command requires the matching verified image and completed recovery receipt.
It exports the published image and verifies the unified tarball's SHA-256 against
the fingerprint. Inspection streams the archive without extraction or booting:
machine-id must be empty, the bare-image marker must match, source must be empty,
and SSH host keys, authorized keys, Tailscale state, root npm/netrc credentials,
shell history and provisioning directories must be absent. It checks these named
infrastructure artifacts, not arbitrary application files for secret content.
The archive layout and identifier follow the
[Incus image format](https://linuxcontainers.org/incus/docs/main/reference/image_format/).

An offline-compiled static TCP fixture runs in a scratch Docker image on each test
box, publishing IPv4 port 38081 on all box addresses. No registry image is pulled.
Positive controls prove local and host/peer Tailscale access. Negative probes check
host-to-box and peer-to-peer bridge access; a temporary host listener tests
box-to-host bridge denial. Existing firewall rules are recorded, not modified.
These probes do not packet-test forwarding to a separate physical LAN machine or
k3s destination, IPv6 service publication, or laptop browser access. Named fixture
containers are removed after the checks; source and fixture images are retained.

`--retire-tests` performs cleanup only after every check passes. Each test box gets
a stopped HDD backup and rootfs export. The command logs it out of Tailscale, stops
it gracefully, detaches its HDD device, and deletes only that test instance.
HDD directories, images, archives, proof receipts and the stopped rootfs retained
by the recovery test remain. `sean-dev-pilot` is never selected. Omitting the flag
runs checks without instance retirement.

Evidence and per-instance retirement phases live under
`/var/lib/chart-incus/boundary-proofs/<build>/`. An existing proof directory refuses
blind reruns; inspect failed/partial state before recovery.

Tailnet inventory removal is separate from local instance deletion/logout. The
command prints the exact test names and node IDs for removal by a tailnet Owner,
Admin or IT admin through the console or API. Until that removal is confirmed,
device cleanup is incomplete. See
[Tailscale device removal](https://tailscale.com/docs/features/access-control/device-management/how-to/remove).

## Recreate while retaining identity and data

Coordinate a pause of every laptop session targeting the box. The operator then
reviews the plan before applying:

```sh
python3 incus/boxes.py recreate --config "$CHART_HOST_CONFIG" \
  --box <box> --image <verified-fingerprint> --sync-paused
sudo python3 incus/boxes.py recreate --config "$CHART_HOST_CONFIG" \
  --box <box> --image <verified-fingerprint> --sync-paused --apply
```

The flag attests to coordination; the host cannot prove a laptop session is paused.
The command captures authorized keys, SSH fingerprints and Tailscale node identity,
stops the box, exports its rootfs and separately copies HDD data, and proves a
scratch restore. It detaches the HDD from the old stopped instance and renames
that instance to `retained-<id>`. The replacement gets the verified bare image and
the existing required HDD attachment. Provisioning reuses SSH host keys and
Tailscale state; it does not generate replacements for missing retained identity.
Recreation compares those identities before reporting success.

The old SSD instance, independent rootfs export, data backup and scratch restore
remain. Existing source/dependencies/config on the old SSD do not populate the
replacement automatically. The laptop agent reconciles its paused sessions to
empty mirrors, reinstalls dependencies and restores its private configuration.
No application dataset is selected, migrated, cleared or initialized by recreation.

Failure leaves a phase record under `/var/lib/chart-incus/recreations/`; do not
rerun blindly, start the retained instance or attach the same HDD to both. Inspect
the record, old instance/device state and verified backups. Recovery is an operator
procedure: keep both instances stopped, select one rootfs, attach the original HDD
only to that instance, verify idmapping/host keys, and start only that copy. Never
run two copies of an enrolled identity. Automated rollback and arbitrary recovery
from a deleted instance are outside this command.
