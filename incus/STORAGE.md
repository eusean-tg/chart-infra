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

The experiment changes neither the published image nor developer boxes, host
AppArmor policy or the original trial result. Successful cycles support the
workaround on the modified fixture; image acceptance and storage migration remain
separate checks. `NonBlocking=yes` controls socket-activation descriptor flags;
it does not grant rsyslog permission to receive signals under AppArmor.
[systemd service configuration](https://github.com/systemd/systemd/blob/v259/man/systemd.service.xml)

Timings are synthetic and affected by filesystem caches and other host workloads.
They establish neither cold dependency-install performance nor application hot
reload latency. Application startup, source sync and representative dependency
operations need their own acceptance during a reversible migration.

## Migration boundary

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
