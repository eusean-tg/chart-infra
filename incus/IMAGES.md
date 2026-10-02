# Bare images, personal boxes and backups

The image contains Ubuntu 26.04, Docker/Compose, SSH, unenrolled Tailscale, the
guest input firewall, native build tools, Python, Git, curl, jq and nvm 0.40.3.
There is no Node version selected, repository content, service image, app CLI,
application configuration or registry token. Developers configure their boxes
using [the developer guide](DEVELOPER.md) and their repositories' instructions.

The host foundation is in [README](README.md). Commands below require operator
sudo for mutations. They do not update existing boxes or laptop sessions.

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
retained build. Partial builds use a new name after diagnosis.

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

Give the laptop agent the box name, SSH host-key fingerprint and developer guide.
Verify both operator/laptop `tailscale ping` and SSH; confirm intended teammate
API access separately against tailnet ACLs. Host Incus access stays with operators.

## Nightly HDD backups

Policy: **04:00 Asia/Kuala_Lumpur, seven days of completed nightly copies**. A backup
stops one box gracefully, copies its HDD directory, then restores its prior box
running/stopped state. There is no forced stop. Apps return only according to the
developer's startup configuration; a process launched manually does not restart
because the box does. Failed copies retain partial artifacts and attempt to restart
an originally running box. Failure is visible in the systemd service journal.

Backups live under `/mnt/hdd/shared-dev/backups/boxes/<UTC timestamp>/<box>/`.
They protect against accidental changes/deletion. The source and backups share an
HDD, so HDD failure needs an additional external/off-machine backup. SSD source,
node_modules, `/root/.npmrc` and arbitrary files outside `/srv/chart/data` are not
included in nightly copies. Place irreplaceable private config under HDD or export
it separately. No application consistency hook is needed while the box is stopped.

Prove a manual copy and restore on a test box before enabling the timer:

```sh
sudo python3 incus/backup.py backup --config "$CHART_HOST_CONFIG" \
  --box <test-box> --apply
sudo python3 incus/backup.py restore-check --config "$CHART_HOST_CONFIG" \
  --backup /mnt/hdd/shared-dev/backups/boxes/<stamp>/<test-box> \
  --scratch /mnt/hdd/shared-dev/restore-checks/<unique-name> --apply
sudo python3 incus/backup.py install-timer --config "$CHART_HOST_CONFIG" --apply
```

`restore-check` verifies the archive checksum, extracts into a new scratch directory
and compares file contents/links. It preserves numeric ownership, rejects escaping
links/devices, starts no service and never overwrites a live dataset. Archives
retain GNU tar ACL/xattr metadata; this scratch check validates content/links, not
a full ACL/xattr round trip. Inspect filesystem-specific restore needs before live
recovery. An archive with an external symlink is retained but needs a separately
reviewed restore method; automatic scratch extraction refuses it.

The timer operates only on registered personal boxes (`user.chart-box=chart-bare-v1`)
with their expected HDD devices and markers. The retained managed pilot is excluded
from automatic stops until explicit migration; a manual backup can target it. It runs from a root-owned tool snapshot, not a developer-writable checkout.
Missed schedules do not trigger daytime catch-up (`Persistent=false`). Pruning
covers only completed nightly HDD-only generations older than seven days with a
newer completed copy. Manual backups, rootfs exports and incomplete generations
are retained. A failed nightly run skips pruning. Monitor HDD/SSD space and inspect
`systemctl status chart-box-backup.timer` and `journalctl -u chart-box-backup.service`.

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
