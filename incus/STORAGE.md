# HDD storage trial

Use `storage_trial.py` to test an HDD-backed Incus pool before migrating developer
instances. The trial creates a directory-backed pool named `hdd` at
`<hdd_mount>/shared-dev/incus` on the registered ext4 filesystem. It does not
repartition, format an existing device, change the registered default pool or
move any existing instance.

The directory driver stores root filesystems directly on HDD. Image unpacking,
instance copies and snapshots use ordinary filesystem copies rather than btrfs
clones. These operations can take longer and consume more space. Compare the
workload before choosing the pool for active development.
[Incus 6.0.5 directory storage](https://github.com/lxc/incus/blob/v6.0.5/doc/reference/storage_dir.md)

## Run the trial

Use the registered host configuration, a unique trial ID and an accepted bare
image fingerprint. Plan mode validates the local configuration and prints scope;
it does not verify privileged Incus state.

```sh
CHART_HOST_CONFIG=/absolute/private/path/host.json
CHART_IMAGE=<full-verified-bare-image-fingerprint>
CHART_TRIAL=hdd-check
python3 incus/storage_trial.py --config "$CHART_HOST_CONFIG" \
  --trial "$CHART_TRIAL" --image "$CHART_IMAGE"
sudo python3 incus/storage_trial.py --config "$CHART_HOST_CONFIG" \
  --trial "$CHART_TRIAL" --image "$CHART_IMAGE" --apply
```

Apply requires local operator sudo. It validates host/pool ownership, HDD identity,
capacity, candidate acceptance and unused fixture names. It refuses an existing
unregistered pool directory. An existing owned HDD pool must match the expected
driver and source path.

The two fixtures are `chart-storage-test-<trial>-ssd` and
`chart-storage-test-<trial>-hdd`. They use the accepted application-free image with
isolated UID maps and no developer data attachment or TUN device. No SSH keys,
Tailscale enrollment, source checkout or credentials are supplied. The first
fixture uses the registered source pool; the second uses the HDD pool.

Each fixture measures boot time, writes and flushes 4096 small synthetic files,
measures a cached content scan, builds a static scratch Docker image without
network access, executes it and verifies data/Docker execution after a restart.
No registry pull or application API is used. File flushing is limited to fixture
files; the trial does not evict host caches or run a host-wide filesystem sync.

## Results and retention

The private receipt is `/var/lib/chart-incus/storage-trials/<trial>/proof.json`.
Successful fixtures are stopped, their ownership/devices rechecked, and only those
synthetic instances are deleted. The pool, its cached image and receipt remain.
Existing instances, backup enrollment, timer and default host configuration remain
unchanged. No additional tailnet device is created.

Failure retains its fixture, pool and phase record and attempts a graceful stop
of the owned running fixture. There is no force stop or automatic deletion on
failure. Inspect the phase and actual Incus state before manual recovery; an
existing receipt refuses a blind rerun. Use a new trial ID only after accounting
for retained resources.

A failed graceful stop can leave the fixture running. The script does not repeat
a failed stop or force it off. The receipt saves partial file measurements before
Docker checks, identifies the failed step, and records cleanup errors separately
from the original failure. A partial measurement is not a passed trial.

### Syslog shutdown experiment

`tests/storage_syslog_live.py` tests an rsyslog `NonBlocking=yes` service drop-in
on the retained HDD fixture of an existing trial. Use it only after diagnosing
PID 1 blocked in `flush_fd()` on the blocking `/run/systemd/journal/syslog` socket.
It checks original trial identity and exact fixture devices/configuration before
mutation. Plan mode prints scope without contacting privileged Incus state:

```sh
python3 tests/storage_syslog_live.py --config "$CHART_HOST_CONFIG" --trial "$CHART_TRIAL"
sudo python3 tests/storage_syslog_live.py --config "$CHART_HOST_CONFIG" --trial "$CHART_TRIAL" --apply
```

Apply saves the guest journal and file measurements to
`/var/lib/chart-incus/storage-trials/<trial>/syslog-experiment.json`, installs
`/etc/systemd/system/rsyslog.service.d/chart-storage-nonblocking.conf` inside that
fixture, and force-stops it once to recover the stuck PID 1. It then tests three
boot/graceful-stop cycles, the actual nonblocking socket flag, rsyslog file
delivery, Docker execution and synthetic-file retention. It retains the fixture
and both receipts. A failed graceful stop has no force-stop fallback.

Guest commands disable stdin and terminal allocation. If an attempt fails before
installing the drop-in, inspect its receipt and use `--attempt <unique-name>` to
retain a separate retry receipt. The default is `syslog-experiment.json`; named
attempts use `syslog-experiment-<name>.json`. A retry refuses an existing drop-in
or receipt rather than silently repeating mutations. Descriptor checks inspect
both PID 1 and rsyslog for the same syslog socket inode.

The experiment changes neither the published image nor developer boxes, host
AppArmor policy or the original trial result. Successful cycles support the
workaround on the modified fixture; image acceptance and storage migration remain
separate checks. `NonBlocking=yes` controls socket-activation descriptor flags;
it does not grant rsyslog permission to receive signals under AppArmor.
[systemd service configuration](https://github.com/systemd/systemd/blob/v259/man/systemd.service.xml)

### Existing-box syslog rollout

After the `stdin-fixed` experiment passes, `incus/syslog_fix.py` verifies rsyslog
replacement on that retained stopped fixture before modifying the selected
running personal box:

```sh
python3 incus/syslog_fix.py --config "$CHART_HOST_CONFIG" --trial "$CHART_TRIAL" --box <personal-box>
sudo python3 incus/syslog_fix.py --config "$CHART_HOST_CONFIG" --trial "$CHART_TRIAL" --box <personal-box> --apply
```

The helper installs `chart-nonblocking.conf`, reloads unit definitions, maps the
guest's single rsyslog process into the host PID namespace, and sends TERM from
host root through a PID descriptor. This avoids the denied signal path from the
guest manager and prevents PID reuse from selecting another process. It has no
KILL fallback. A failed fixture restart or graceful stop prevents live-box
mutation. Each target's replacement must be unique, nonblocking and delivering
logs. The personal box and its application services are not restarted.

Receipts live under `/var/lib/chart-incus/syslog-rollouts/<box>/`; existing receipts
refuse a blind rerun. The personal box also receives a recovery copy at
`/srv/chart/data/private/systemd/chart-nonblocking.conf`. Inspect a partial failure
before recovery; a failed logger restart can leave file-based logging stopped
while journald continues. The helper does not disable confinement or repair the
underlying AppArmor signal policy. Preserve both fixture and receipts.

Timings are synthetic and affected by filesystem caches and other host workloads.
They establish neither cold dependency-install performance nor application hot
reload latency. Application startup, source sync and representative dependency
operations need their own acceptance during a reversible migration.

## Migration boundary

### Cross-pool fixture

Before a personal-box move, verify both transfer directions with an isolated
fixture and a synthetic idmapped HDD attachment:

```sh
sudo python3 incus/storage_move.py verify --config "$CHART_HOST_CONFIG" \
  --trial <unique-trial-id> --image "$CHART_IMAGE" --apply
```

This starts an unenrolled bare fixture on the registered source pool, applies the
syslog mitigation, and verifies files owned by guest UID 0 and UID 1000 on both
rootfs and attached HDD storage. It moves the stopped fixture to `hdd`, back to
the source pool, and finally to `hdd`. Each boot checks file content, ownership,
modes, UID/GID maps and nested Docker execution. No developer files are attached.
The stopped fixture and synthetic data remain after success; failures retain
their phase and actual instance state without forced cleanup.

Evidence is `/var/lib/chart-incus/storage-moves/<trial>/verify.json`; synthetic
data is `<hdd_mount>/shared-dev/storage-fixtures/<trial>`. A trial name or data
collision refuses a rerun. This check proves the fixture transfer path, not
application latency or a live deployment migration.

Incus 6.0.5 implements a local pool move through a temporary copy. Copy creation
can allocate another isolated UID range, replace the cloud-init instance ID and
queue copy templates. Before boot, the helper restores the saved base/next UID
mapping and boot metadata through the Incus API. It requires the original current
and disk mappings to remain intact, checks other instances across all projects
for overlapping UID/GID ranges, and rejects other configuration changes. No host
file ownership rewrite or confinement change is performed. Coordinate with other
host operators: the chart lock does not lock independent Incus clients.
Each move retains a `move-<ordinal>-metadata.json` receipt beside `verify.json`.
[Incus 6.0.5 local move implementation](https://github.com/lxc/incus/blob/v6.0.5/cmd/incusd/instance_post.go)

For a trial whose first transfer completed but the identity check stopped it
before boot, use the explicit fixture recovery command:

```sh
sudo python3 incus/storage_move.py resume-fixture --config "$CHART_HOST_CONFIG" \
  --trial <failed-trial-id> --image "$CHART_IMAGE" --apply
```

Recovery requires the original failed-first-transfer receipt, exact stopped HDD
fixture, unchanged data marker and original file hashes/maps. It preserves
`verify.json`, writes `resume.json` plus `resume-<ordinal>-metadata.json`, and tests
HDD→source-pool→HDD with file, mapping, Docker and graceful-stop checks. A partial
recovery retains its receipt and refuses a blind retry. This command cannot
target a personal developer box. A prepared recovery is not live acceptance.

### Per-box placement

The optional host-config field `instance_pools` explicitly maps migrated personal
box names to `hdd`. Unlisted names use `pool`. The mapping changes the expected
root device for lifecycle and backup ownership checks; it does not move an
instance. Host checks require the owned `dir` pool at the registered HDD path.
The installed backup-tool snapshot and both host-config copies must recognize the
mapping at cutover. Do not edit this field before the corresponding stopped
instance transfer is verified.

A successful trial does not authorize a raw `mv` of mounted pool files. Incus
supports moving a stopped instance to another pool with `incus move --storage`.
A deployment migration must also preserve HDD attachments, numeric ownership,
SSH/Tailscale identity, source paths and prior running/stopped state.
[Incus 6.0.5 storage moves](https://github.com/lxc/incus/blob/v6.0.5/doc/howto/storage_move_volume.md)

Before moving an active instance, coordinate paused laptop sessions, verify an
independent backup/rootfs export and define rollback. Update host ownership checks
and the installed backup-tool snapshot for the selected pool as part of the same
cutover. Exclude concurrent backup, recreation and infrastructure apply operations.
Backups remain on SSD/NVMe; moving rootfs to HDD does not add rootfs content to the
nightly data-directory backup.

The trial command performs none of those migration operations. Record its result
and reconcile the target pool with infrastructure-management plans before cutover.

### Move a personal box

`storage_migrate.py` moves one running personal box from the default SSD pool to
the owned HDD pool. It requires a verified matching-image round trip, no Incus
snapshots on the selected box, a nonblocking syslog socket and paused laptop sync.
The operator must flush and pause the selected laptop mapping first; the host
cannot verify laptop session state. Leave sessions paused through acceptance or
rollback. Coordinate other host operators to exclude independent Incus changes.

```sh
python3 incus/storage_migrate.py move --config "$CHART_HOST_CONFIG" \
  --box <personal-box> --trial <verified-trial-id> --migration <unique-migration-id>
sudo python3 incus/storage_migrate.py move --config "$CHART_HOST_CONFIG" \
  --box <personal-box> --trial <verified-trial-id> --migration <unique-migration-id> \
  --sync-paused --apply
```

The apply holds the chart infrastructure lock and pauses the nightly timer. It
saves the installed backup-tool files, installs the compatible tool snapshot,
stops the box gracefully, and creates an independent data archive and rootfs
export on SSD. It verifies a scratch data restore and reserves capacity for
rollback before moving rootfs. The selected box keeps its HDD disk device,
guest paths and UID map. Copy-time identity changes are repaired while stopped
using the fixture-tested procedure above.

A second stopped rootfs export must match the first for file contents, numeric
ownership, modes, symbolic links, ACLs and extended attributes. Only then does
the helper update both host-config copies with the box's `instance_pools` entry.
The installed backup tool must validate ownership, produce a data backup from
the HDD placement and pass another scratch restore before boot. After boot,
SSH-key hashes, authorized keys, machine-id, Tailscale node ID/IPs and actual
UID/GID maps must match. The timer returns to its original active/inactive state.

Rootfs verification streams each archive once, reuses preceding-file hashes for
hard links, and reports every 10,000 rootfs entries. It never seeks backward
through gzip to hash a hard-linked file again. Backup/export and copy phases can
remain quiet while their child commands run; CPU activity alone does not prove
useful progress. Inspect the saved phase and active child before interrupting.

Success reports `moved-awaiting-application-and-laptop-acceptance`. Check the
developer's actual application health, browser flow and representative startup
performance, then resume/flush its existing laptop mapping and verify hot reload.
No replacement image, Tailscale enrollment or source-sync session is needed.
These checks do not boot a restored rootfs archive; the scratch restore covers
the attached data. The original pool remains available for rollback.

Receipts and backup-tool originals live under
`/var/lib/chart-incus/storage-migrations/<migration>/`. SSD manual backup
generations retain `data.tar`, `rootfs.tar.gz`, `rootfs-hdd.tar.gz` and metadata;
scratch data lives under `/var/backups/chart-incus/restore-checks/storage-<migration>`
and its `-after` sibling. These may contain private files and credentials. Retain
them until explicit retirement; routine nightly pruning excludes them.

If initial rootfs verification was interrupted after a completed backup, use:

```sh
sudo python3 incus/storage_migrate.py resume-backup --config "$CHART_HOST_CONFIG" \
  --migration <migration-id> --sync-paused --apply
```

This continuation requires a failed `stopped-independent-backup` or
`verifying-original-rootfs` phase, a completed backup, unchanged source-pool
placement/configuration and a stopped box. It preserves the interrupted receipt
as `interrupted-backup.json`, verifies both archive checksums and continues from
rootfs verification. It creates neither a replacement first backup nor a second
instance. A continuation receipt collision refuses another blind retry.

Failure preserves the recorded phase and actual instance state. The box and
nightly timer can remain stopped; inspect the receipt instead of repeating
`move`. With sync still paused, use:

```sh
sudo python3 incus/storage_migrate.py rollback --config "$CHART_HOST_CONFIG" \
  --migration <migration-id> --sync-paused --apply
```

Rollback validates known old/new configuration, restores any verified copy-time
metadata and moves the stopped rootfs back through Incus. It restores both host
configurations and the saved backup-tool snapshot, then boots and checks identity
before restoring the timer. It does not overwrite data from an archive. Foreign
configuration, changed backup-tool files, identity changes or inadequate SSD
capacity refuse rollback for operator review. Application and laptop acceptance
remain required after rollback. Rootfs exports are retained for separate disaster
recovery if a supported reverse move cannot complete.

`prep.py status` reports HDD free space when per-box HDD placement is registered.
Review at 70% HDD use and pause heavy work at 85%, preserving at least 10 GiB.
SSD backup reserve remains 25% plus 1 GiB. Monitoring is explicit, without a
background cleanup job. Other instances, image caches and the default pool
remain unchanged by this single-box operation.

### Replace an interrupted pre-move box with a fresh HDD box

For an operator-selected fresh start after an interrupted initial backup check:

```sh
sudo python3 incus/storage_migrate.py recreate --config "$CHART_HOST_CONFIG" \
  --migration <migration-id> --sync-paused --apply
```

This recovery path requires the original stopped source instance and its completed
data backup. It copies authorized keys, verifies a scratch data restore, detaches
the HDD from the old instance and renames that stopped instance to
`retained-ssd-<id>`. It registers HDD placement and uses the existing bare-box
creation/adoption procedure with the same accepted image. SSH host keys,
Tailscale state, databases and private files stay in the existing HDD attachment.
Source, dependencies and other rootfs files stay in the old SSD instance.

The replacement receives the syslog mitigation and must retain SSH keys and
Tailscale node/IP identity before the original backup timer state is restored.
OS machine-id and UID-map allocation belong to the fresh instance. Applications
remain the laptop agent's responsibility: reconcile its paused sessions, restore
private configuration and startup from retained data, and reinstall dependencies.
Do not re-enroll Tailscale or initialize an empty database over retained data.

Evidence is `recreate.json`, `recreate-authorized_keys` and `recreate-syslog.json`
under the selected migration directory; scratch data is
`/var/backups/chart-incus/restore-checks/recreate-<migration>`. Keep the old instance
stopped without its data attachment. The original migration receipt is marked
superseded after success. Failure retains its actual phase for operator recovery;
do not retry blindly or use the in-place migration rollback for a recreated box.
