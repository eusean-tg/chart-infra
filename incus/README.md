# Host, image and box operations

Host operators use this guide for preparation, images, personal-box lifecycle
and backups. Commands run from the repository root. Read [architecture](ARCHITECTURE.md)
for storage/access boundaries and [developer setup](DEVELOPER.md) for applications.
Establish the host, configuration and exact target before applying a mutation.

- [Host preparation](#inspect-and-select-the-host)
- [Pool registration](#pool-registration)
- [Image build and acceptance](#build-and-accept-a-generic-image)
- [Box creation](#create-a-developer-box) and [enrollment](#enrollment-and-developer-handoff)
- [Backups](#backups), [recovery test](#test-image-recovery) and [network acceptance](#published-artifact-network-boundaries-and-test-retirement)
- [Recreation](#recreate-while-retaining-identity-and-data)
- [Capacity checks](#capacity-and-operator-checks)

Host mutations require operator sudo. Review the plan before `--apply`; preserve
unrelated host services, data and synchronization sessions.

## Inspect and select the host

Run from the chart-infra checkout:

```sh
python3 incus/prep.py inspect
```

Create a private copy of `host.example.json` outside Git. Set `machine_id` and
`hdd_uuid` from inspection. Confirm the mount, project, pool name and bridge
subnet. Example values in the template are not discovery results for another
machine. The script checks all IPv4 route tables, including VPN routes.
Host setup selects a single connected private LAN on the main default uplink.
Use `--lan-cidr <subnet>` when selection is ambiguous; a VPN, box bridge or k3s
network is not accepted as the LAN.

Set explicit absolute paths for subsequent commands:

```sh
CHART_HOST_CONFIG=/absolute/private/path/host.json
CHART_ARTIFACTS=/absolute/cache/path/incus-artifacts
python3 incus/prep.py host-prepare --config "$CHART_HOST_CONFIG"
```

Before applying, record unrelated workload health, storage identities and
endpoint reachability. Privileged preparation saves the original host
firewall, routes and package inventory under `/var/lib/chart-incus/`. These files
are root-only. The tool does not read Kubernetes Secrets or developer tokens.

## Prepare the host

```sh
python3 incus/prep.py fetch --artifacts "$CHART_ARTIFACTS"
sudo python3 incus/prep.py host-prepare --config "$CHART_HOST_CONFIG" --apply
```

Review the host plan and scripts before the sudo invocation. Host preparation
refreshes APT metadata, checks a simulated pinned install for removals and
unrelated installed-package changes, then installs. It does not upgrade Docker,
restart k3s, repartition disks or format an existing block device. A foreign
Incus pool/project/network with a matching name is refused. Container support
uses `incus-base` and `incus-client`, with explicit `dnsmasq-base` for the bridge;
the QEMU/VM metapackage is outside this container setup. Existing owned
resources must match their configuration; they are not silently repurposed.

The pool is a new sparse btrfs backing file under `/var/lib/incus/disks/` on the
root SSD. Preparation checks that fully allocating 200 GiB would still leave
25% of the host filesystem free. Incus and `btrfs-progs` versions are pinned in
`versions.lock.json`. The Ubuntu image and Tailscale package have SHA-256 pins;
unavailable pins fail rather than selecting a floating replacement. Transitive
APT dependencies are resolved by Ubuntu and recorded per box; this is not a
byte-identical package-repository snapshot.

The host firewall has its own nftables table, a dedicated iptables forwarding
chain and three bridge-only DHCP/DNS input rules. Restrictions run before the
Docker-coexistence accepts. Bridge input accepts established replies, so the
host's Tailscale hole-punch traffic receives its responses. Forwarding permits
UDP **source port 41641** from the box bridge to the selected LAN; peer destination
ports can vary. The guest Tailscale service fixes its listening/source port to
41641. This exception precedes the private-network rejection and follows the
sibling-bridge/VPN denials. It does not allow new LAN TCP connections.

Other box underlay access to private networks, host management, WARP and the host
tailnet interface remains denied. A root user in the box can originate UDP from
41641; this is a port-scoped exception, not WireGuard packet authentication.
Tailnet ACLs independently govern traffic inside the encrypted tunnel, including
box access to the host's Tailscale address. IPv6 underlay is
disabled; IPv6 inside the Tailscale tunnel remains independent. UDP 41641 is the
only unsolicited underlay box ingress allowance. NAT Internet access permits
package preparation; it is **not** application offline-egress enforcement.

The root-owned `chart-incus-firewall.service` reapplies after Docker/Incus
restarts. It never flushes their tables or shared chains. Inspect effective
rules and test coexistence; another daemon or workstation firewall can still
block traffic. Verify direct connectivity using `tailscale ping <box>` from the
PC and a LAN laptop; a successful relayed ping does not prove the direct path.
Tailscale retains DERP fallback for peers that cannot establish a direct path.
Application ports remain bound to Tailscale addresses; LAN support concerns
encrypted Tailscale transport, not exposing Mongo or HTTP on the LAN.

Initial host setup persists `fs.inotify.max_user_instances=1024` and
`fs.inotify.max_user_watches=1048576` in
`/etc/sysctl.d/99-chart-incus-inotify.conf`. It applies only that file with
`sysctl --load`; it does not reload unrelated host sysctls. These kernel settings
belong on the host, outside a golden image, and are visible inside system
containers. They raise available watch capacity; they do not reserve RAM.

For an already prepared host, use the explicit update operation:

```sh
sudo python3 incus/prep.py host-tune --config "$CHART_HOST_CONFIG" \
  --lan-cidr <connected-lan-subnet> --apply
```

It validates the nftables transaction before installation, retains a copy of the
recognized prior firewall script, reloads the dedicated firewall service and
applies the two inotify values. It refuses unrecognized local firewall edits or
conflicting LAN/sysctl configuration. No host or box reboot is required. Verify
the effective values with `sysctl fs.inotify.max_user_instances
fs.inotify.max_user_watches` on both host and box after applying.

## Pool registration

Host configuration selects a default `pool`. Optional `instance_pools` entries
select `hdd` for individual names; unlisted names use the default. For example:

```json
{
  "pool": "ssd",
  "instance_pools": {"alex-dev": "hdd"}
}
```

This is a fragment of the host configuration, not a complete configuration file.
The preparation template creates a 200 GiB SSD btrfs pool. Per-box HDD placement
requires an existing owned `dir` pool named `hdd`, with source
`<hdd_mount>/shared-dev/incus` on the registered HDD. Host checks validate its
ownership marker, path and filesystem. Creating a pool does not change placement.

Both operator and installed host configurations, and the installed backup-tool
snapshot, must recognize the selected placement. The mapping controls creation
and ownership checks; editing it does not move an existing instance. Coordinate
placement changes with the operator and exclude concurrent lifecycle/backup work.
Do not relocate mounted pool directories with filesystem commands.

## Image contents and preparation

The image contains Ubuntu 26.04, Docker/Compose, SSH, unenrolled Tailscale, the
guest input firewall, native build tools, Python, Git, curl, jq and nvm 0.40.3.
There is no Node version selected, repository content, service image, app CLI,
application configuration or registry token. Developers configure their boxes
using [the developer guide](DEVELOPER.md) and their repositories' instructions.

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
Both test boxes must contain the exact rsyslog `NonBlocking=yes` drop-in and show
nonblocking syslog descriptors in PID 1 and rsyslog; the receipt records those flags.
Application tests, repository secret scanning and seeded-source promotion are not
part of image acceptance. A failed check records no verified image; inspect the
retained build.

For an OS-only maintenance image with unchanged networking, operators can select
local acceptance without enrolling test nodes:

```sh
sudo python3 incus/image.py image-verify --config "$CHART_HOST_CONFIG" \
  --build "$CHART_BUILD" --local-only --apply
```

This runs the generic runtime checks, verifies distinct machine/SSH identities,
gracefully stops and restarts each disposable test box, rechecks syslog descriptors
and identities, and inspects the exported image for sanitization and the drop-in.
The receipt records `verification_scope: local-only` and `network_checks: skipped`.
It permits creation from that fingerprint but proves neither tailnet access nor
recreation with retained enrolled identity. No network/recovery proof is inherited
from another image. Use full acceptance for networking or identity-lifecycle changes;
a locally verified build can later run `image-verify` without `--local-only` after
enrollment. Stop failures retain the fixtures; no forced shutdown is used.

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

## Enrollment and developer handoff

1. Enroll the box interactively with `tailscale up --hostname=<box>
   --accept-routes=false --accept-dns=true --ssh=false`. Use the local operator
   console; do not put auth keys in scripts, Git, logs or chat. Confirm assigned
   IP/name, tailnet peer policy and reconnection. Do not advertise routes, an
   exit node or Funnel. SSH uses the developer's key, not Tailscale SSH policy.
   For personal developer boxes, use that developer's account in the existing
   team tailnet. User ownership and peer network access are separate: verify the
   tailnet policy permits intended teammate APIs and operator access. Centrally
   managed tagged boxes require a separate tailnet-admin policy decision.
2. From the laptop, verify the SSH host-key fingerprint through the host
   operator, then connect as `root@<box>`. This is root inside the unprivileged
   box. Do not disable host-key checking. Verify service access from a teammate
   during application acceptance; successful enrollment is not that proof.

Give the laptop agent the box hostname, trusted SSH host-key fingerprint,
[developer guide](DEVELOPER.md) and `skills/chart-box/` with its helper scripts.
An operator who needs box SSH requires an explicitly authorized public key;
the developer's laptop key does not authorize another device.

## Memory and storage budgets

Creation sets `limits.memory=8GiB`, a hard memory ceiling without reserving RAM.
No CPU cap is set. Applying this setting to an existing box is a live Incus
configuration change; inspect its memory usage before reducing the ceiling.
Ownership validation accepts retained boxes without a memory cap, so their backup
and recovery remain available.

Before capping an existing box, refresh `prep.py` and `host_common.py` in the
root-owned `/var/lib/chart-incus/backup-tool/` snapshot from the tested checkout.
The old validator rejects `limits.memory`. Hold `/run/lock/chart-incus-prep.lock`
while updating the snapshot and box configuration to exclude scheduled backups.
Verify `limits.memory` in the expanded Incus config and `/sys/fs/cgroup/memory.max`
inside the running box (`8589934592` bytes for 8 GiB).

The storage budget is 256 GiB per box across rootfs and the retained data
attachment. It is a planning budget, not allocated capacity or an enforced quota.
The HDD `dir` pool requires ext4/XFS project quotas for enforcement; the external
data attachment needs its own quota accounting. Do not set a rootfs-only size and
describe it as a combined box limit. Quota enablement requires a separate host
storage procedure; do not repartition or format the disk.

## Backups

Policy: **04:00 Asia/Kuala_Lumpur, seven days of completed nightly copies**. A backup
stops one box gracefully, copies its HDD directory, then restores its prior box
running/stopped state. There is no forced stop. Apps return only according to the
developer's startup configuration; a process launched manually does not restart
because the box does. Failed copies retain partial artifacts and attempt to restart
an originally running box. Failure is visible in the systemd service journal.

Backups live under `/var/backups/chart-incus/boxes/<UTC timestamp>/<box>/` on the
host root SSD/NVMe filesystem, separate from the HDD source. An off-machine copy is
needed for loss of the whole PC. Rootfs source,
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

Each nightly directory contains `data.tar` and `backup.json`. Manual backups
with `--rootfs` also include `rootfs.tar.gz`. Scratch restores belong under
`/var/backups/chart-incus/restore-checks/`. See the [coverage table](ARCHITECTURE.md#backup-coverage)
for which files require a separate rootfs or host backup.

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
`chart-test-*`, `chart-bare-*` and `retained-*` cannot enroll;
manual backup remains available. `unenroll --box <name> --apply` removes a box
from the schedule without stopping it or removing copies. Installation requires
at least one enrolled box. The timer runs from a root-owned tool snapshot under
`/var/lib/chart-incus/backup-tool/`. Updating the checkout does not update that
snapshot; review and install changes explicitly without changing enrollment.
Missed schedules do not trigger daytime catch-up (`Persistent=false`). Pruning
covers only completed nightly HDD-only generations older than seven days with a
newer completed copy. Manual backups, rootfs exports and incomplete generations
are retained. A failed nightly run skips pruning. Monitor HDD/SSD space and inspect
`systemctl status chart-box-backup.timer` and `journalctl -u chart-box-backup.service`.

To run the installed nightly service immediately and inspect its result:

```sh
sudo systemctl start chart-box-backup.service
systemctl show chart-box-backup.service -p Result -p ExecMainStatus
journalctl -u chart-box-backup.service --no-pager -n 40
```

The start command waits for completion and can produce no output on success.
Check actual application readiness after the box returns. A manual trigger of
this service still creates nightly-policy generations and leaves the timer intact.

## Test image recovery

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
by the recovery test remain. Only the recorded test pair is selected. Omitting the flag
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

The old instance, independent rootfs export, data backup and scratch restore
remain. Existing source/dependencies/config in the old rootfs do not populate the
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

## Capacity and operator checks

```sh
sudo python3 incus/prep.py status --config "$CHART_HOST_CONFIG"
systemctl status chart-box-backup.timer
```

Inspect pool usage, btrfs data/metadata allocation and host free space after
image builds, dependency installs, snapshots and new boxes. Use guest
`docker system df -v` and the selected package manager's store path to locate growth.
Review at 70% pool/HDD usage, below 25% host SSD free, or unexpected growth.
Pause optional heavy work at 85% or earlier btrfs allocation pressure; keep at
least 10 GiB HDD free. Backup checks require 25% host-root free plus 1 GiB.
No background monitor or cleanup job enforces the review thresholds.

Provisioning inherits host timezone and sets `boot.autostart=false`. A host reboot
does not start every box. APT upgrades are explicit; guest package timers are
disabled. Starting a box restores its infrastructure services; developers configure
application startup. Do not recursively rewrite HDD ownership to repair idmapping.

For bootstrap checks use `sudo incus exec local:<box> --project <project> -- <command>`;
the host cannot SSH to a bridge address by design. Check mounts, UID maps, runtime
versions and service boundaries on disposable fixtures. Never use existing data
as a fixture or restart unrelated workloads for acceptance.

Offline checks are listed in the [repository README](../README.md#development-checks).
Incus contracts follow the [6.0.5 source](https://github.com/lxc/incus/tree/v6.0.5),
[btrfs driver](https://linuxcontainers.org/incus/docs/main/reference/storage_btrfs/)
and [firewall coexistence](https://linuxcontainers.org/incus/docs/main/howto/network_bridge_firewalld/).
