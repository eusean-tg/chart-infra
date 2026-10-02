# Incus preparation

This directory prepares an Ubuntu 26.04 host foundation and an unprivileged Ubuntu
26.04 developer box. The existing `./chart` commands operate k3s profiles; they do
not operate these boxes. Mutagen over SSH is the box source transport.

After preparation and enrollment, use [Backing services](BACKING.md) for the
separate Mongo/Dragonfly preparation, startup, retention and verification commands.

`prep.py` uses Python's standard library. Preparation requires a host operator
with sudo; developers receive root inside their own box only. Do not grant host
Incus, Docker or Kubernetes access to enable laptop operations.

## Scope

| Command | Effect |
| --- | --- |
| `inspect` | Read host identity, mount, route and capacity information |
| `fetch` | Download and checksum public Ubuntu image and Tailscale artifacts into an explicit cache directory |
| `host-prepare` | Plan by default; `--apply` installs pinned host packages, creates the SSD pool/project/bridge, installs firewall rules with a Tailscale LAN exception and applies persistent inotify settings |
| `host-tune` | Plan by default; `--apply` upgrades the recognized installed firewall for LAN direct connections and applies the inotify settings without reinstalling packages or changing storage |
| `box-create` | Plan by default; `--apply` imports the pinned image and creates a **stopped** box with an isolated UID map, TUN and its own HDD attachment |
| `box-provision` | Plan by default; `--apply` starts that box, inherits the host timezone and installs Docker/Compose, unenrolled Tailscale, key-only SSH and the guest input firewall |
| `status` | Read capacity and image/instance inventory; with sudo, include pool and btrfs allocation details |

No preparation command fetches application source, imports environment files, creates Mongo
data, starts applications, enrolls Tailscale, changes laptop sync sessions or
deletes retained storage. There is no teardown/prune command. No CPU/memory limits
are configured. Instances have `boot.autostart=false`.

These are preparation commands, not an accepted deployment. Live nested-Docker,
firewall, storage and restart checks are required on the target host. A sanitized
golden-image publisher, backend application stack, dataset switching and box-aware Mutagen
freeze/selection helper remain separate implementation work. Do not publish a
provisioned developer box as a golden image: it contains private identities.

## 1. Inspect and select the host

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
CHART_SSH_KEY=/absolute/path/to/developer-public-key.pub
CHART_BOX=sean-dev-pilot
python3 incus/prep.py host-prepare --config "$CHART_HOST_CONFIG"
```

Before applying, record unrelated workload health, Pod and PV/PVC identities and
existing endpoint reachability. Privileged preparation saves the original host
firewall, routes and package inventory under `/var/lib/chart-incus/`. These files
are root-only. The tool does not read Kubernetes Secrets or developer tokens.

## 2. Fetch artifacts and prepare the host

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
the QEMU/VM metapackage is outside this pilot. Existing owned
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

## 3. Create and provision the box

Obtain the developer's intended public SSH key. Do not copy every key from the
host's `authorized_keys`. No private SSH key belongs in the box or repository.

```sh
python3 incus/prep.py box-create --config "$CHART_HOST_CONFIG" \
  --name "$CHART_BOX" --ssh-key "$CHART_SSH_KEY" --artifacts "$CHART_ARTIFACTS"
sudo python3 incus/prep.py box-create --config "$CHART_HOST_CONFIG" \
  --name "$CHART_BOX" --ssh-key "$CHART_SSH_KEY" --artifacts "$CHART_ARTIFACTS" --apply
sudo python3 incus/prep.py box-provision --config "$CHART_HOST_CONFIG" \
  --name "$CHART_BOX" --artifacts "$CHART_ARTIFACTS" --apply
sudo python3 incus/prep.py status --config "$CHART_HOST_CONFIG"
```

Box creation uses no inherited Incus profiles. The root disk is SSD-backed;
`/mnt/hdd/shared-dev/boxes/<box>` is attached at `/srv/chart/data` with required,
idmapped access. Existing data without its registered instance is refused rather
than reinitialized. Preparation never recursively changes host ownership.

Box provisioning reads the host timezone with `timedatectl` and passes it to the
guest setup. The setting belongs in per-box provisioning so a future golden
image cannot impose another host's timezone. Existing boxes can use
`timedatectl set-timezone <host-timezone>` over authorized box SSH. This changes
local time display, not the shared kernel clock; applications that explicitly
log UTC retain their own formatting. Golden-image publication is not implemented.

| Inside the box | Storage |
| --- | --- |
| `/srv/chart/source/<workspace>/<repo>` | SSD source mirror |
| `/srv/chart/cache/pnpm` | Independent SSD package store |
| `/var/lib/docker`, `/var/lib/containerd` | SSD images, runtime state and dependency volumes |
| `/srv/chart/data/datasets/<dataset>/mongo/member-0` | Retained HDD database path; created by explicit backing-service preparation |
| `/srv/chart/data/identity` | Retained HDD SSH host keys, Tailscale state, package inventory and future app identities |
| `/srv/chart/data/backups` | Retained HDD exports |

APT runs only during explicit preparation. Its periodic guest upgrade timers
are disabled to keep package changes deliberate. Repeated `box-create` verifies
and retains a matching instance. Completed `box-provision` refuses to rerun as
an implicit package upgrade. Interrupted preparation retains its state for
inspection; it does not destroy a failed box. A differing root-installed script
or host configuration requires an explicit operator-reviewed update.

## 4. Prove the foundation before enrollment and apps

Use `sudo incus exec local:<box> --project chart-dev -- <command>` for privileged
bootstrap checks. The host cannot SSH to the box's bridge address by design.

1. Inspect host firewall order/counters and bridge port isolation. Compare
   unrelated service/volume identities with the preservation baseline.
2. Verify Ubuntu release, unique machine-id, isolated UID map, TUN, HDD UUID,
   the real data mount and idmapped writes. Record exact package versions and
   Docker/containerd storage paths. Recheck after stop/start; no existing data
   path is a test fixture.
3. Use `runtime-proof.py` for the offline build/container/storage checks described
   below, then pull and execute an explicitly pinned public smoke-test image.
   Preserve the test volume; no `prune` or `down -v`. A successful
   `docker info` alone does not prove unprivileged nested runtime compatibility.
   Add syscall interception only if a concrete failure demonstrates the need.
4. Test host/bridge ingress denial and box underlay denial to host management,
   k3s, LAN and VPN networks, while public package access succeeds. Verify after
   coordinated Docker/Incus restarts; do not restart unrelated workloads just
   to run acceptance. A workstation reboot needs a separate agreed window.
5. Enroll the box interactively with `tailscale up --hostname=<box>
   --accept-routes=false --accept-dns=true --ssh=false`. Use the local operator
   console; do not put auth keys in scripts, Git, logs or chat. Confirm assigned
   IP/name, tailnet peer policy and reconnection. Do not advertise routes, an
   exit node or Funnel. SSH uses the developer's key, not Tailscale SSH policy.
   For personal developer boxes, use that developer's account in the existing
   team tailnet. User ownership and peer network access are separate: verify the
   tailnet policy permits intended teammate APIs and operator access. Centrally
   managed tagged boxes require a separate tailnet-admin policy decision.
6. From the laptop, verify the SSH host-key fingerprint through the host
   operator, then connect as `root@<box>`. This is root inside the unprivileged
   box. Do not disable host-key checking. Verify service access from a teammate
   during application acceptance; successful enrollment is not that proof.

## Mutagen handoff

An operator agent on the PC needs its own explicitly authorized SSH key to run
commands inside the box. The laptop key does not authorize a different PC key.
Adding a PC key grants box-root access only; it does not grant host Incus or sudo
access. Preserve the laptop's existing authorized key. Discover and verify the
intended PC public-key fingerprint before adding it.

For an approved additional public key, use the host operator's authenticated
terminal. Set `CHART_BOX`, `CHART_OPERATOR_KEY` and `CHART_HOST_KEY_REPORT` to
explicit values; the last path receives only the box's public host key:

```sh
sudo incus exec "local:$CHART_BOX" --project chart-dev -- sh -eu -c '
  IFS= read -r chart_key
  test -n "$chart_key"
  test -f /srv/chart/data/identity/guest-prepared
  test -f /root/.ssh/authorized_keys
  test ! -L /root/.ssh/authorized_keys
  test -s /srv/chart/data/identity/ssh/ssh_host_ed25519_key.pub
  if ! grep -qxF -- "$chart_key" /root/.ssh/authorized_keys; then
    printf "%s\n" "$chart_key" >> /root/.ssh/authorized_keys
  fi
  cat /srv/chart/data/identity/ssh/ssh_host_ed25519_key.pub
' < "$CHART_OPERATOR_KEY" > "$CHART_HOST_KEY_REPORT"
```

Compare that trusted public host key with the network-presented key, then record
it in the operator's dedicated known-hosts file. Require strict host-key checking
and use the selected operator identity. A raw `ssh-keyscan` result alone is not
trusted verification. Record additional authorized-key fingerprints privately;
retain the original laptop key and account for both during box reconstruction.

Use laptop Mutagen **0.18.1** with independent, recorded pilot session IDs. It
starts its remote agent over SSH; no Syncthing service, pairing or port 22000 is
needed. Discover laptop paths and prefer separate pilot worktrees so controlled
edits cannot reload the existing k3s profile. Destinations are
`root@<box>:/srv/chart/source/<workspace>/<repo>`.

Reuse the inclusion/exclusion semantics in `sync_common.py`: one-way-safe,
SHA-256, ignored symlinks, no `.git`, dependencies, build outputs, private dotenv
files, keys or credentials, with the documented sample/template exceptions.
Do not paste raw defaults or invent a subtly different secret exclusion policy.
Inspect existing laptop sessions before adding only these new destinations.

The existing `laptop-sync.py` activation protocol targets k3s and is not a box
controller. A box-aware helper still needs implementation and real watcher
acceptance. Until then, do not mark a raw pause as a frozen generation. Initial
activation and dependency changes require flush, pause of only the owned
sessions, included-file fingerprint equality across all three repos, and stopped
app writers. Resume only those sessions after dependencies and selection are
ready. Test save/rename/delete/reconnect and each actual `tsx watch` process.

## Capacity and retention

Run `status` after image/package preparation, dependency installs, snapshots or
new boxes. Record pool consumption, btrfs data/metadata allocation, host SSD
free space, and later per-box `docker system df -v` and pnpm-store usage.
Investigate at 70% pool usage, below 25% host SSD free, or unexpected growth at
any level. Pause optional heavy work at 85% pool usage or earlier btrfs
allocation pressure. Preparation is conservative and refuses at the review
threshold. There is no background monitoring or automatic cleanup.

An Incus snapshot excludes the attached HDD data. Mongo needs independent
consistent backups; restoring a rootfs snapshot requires source sync paused and
schema/dependency compatibility checked. Never clone enrolled identities into
another running box. Stop retains all data; deletion requires an explicit named
target. Existing k3s data and historical Mongo/DNS pilots are outside these
commands.

## Offline runtime proof

Copy `runtime-proof.py` and a verified amd64 statically linked BusyBox binary
into `/root/chart-prep/` in the box over authenticated SSH or Incus file transfer.
Record the binary's package version and SHA-256 on the source PC. Pass that
checksum explicitly; the helper does not download a binary or contact a registry.

Inside the box:

```sh
python3 /root/chart-prep/runtime-proof.py --box <box-name> \
  --busybox /root/chart-prep/busybox --sha256 <verified-sha256>
python3 /root/chart-prep/runtime-proof.py --box <box-name> \
  --busybox /root/chart-prep/busybox --sha256 <verified-sha256> --apply
```

The helper requires the matching box/data marker, completed guest preparation,
an unprivileged UID mapping, SSD Docker storage and no running Docker containers.
It builds a scratch image with a build-time command, writes a synthetic token
through Docker mounts to a named volume and the HDD, reads both from a second
container, and starts that reader again. Build and runtime networking are disabled.
Each run has a unique fixture name. Images, exited containers, the volume, HDD
fixture and build context/report are retained, including on failure. The report
is under `/root/chart-prep/runtime-v1-<id>/report.json`.

This proves nested build/execution and retained reads across container instances;
it does not prove a whole-box stop/start, backups or application persistence.
With only exited fixtures present, an operator can separately restart the box's
Docker daemon and start the retained reader again to test daemon-restart retention.
Use the pinned Node base from `runtime/Dockerfile` for a separate registry-pull and
Node-execution check. No host Docker restart is needed for either check.

## Development checks

```sh
python3 tests/incus_prep.py
python3 -m py_compile incus/prep.py incus/firewall.py incus/runtime-proof.py
bash -n incus/guest-prepare.sh
```

These are offline checks, not live deployment acceptance. Incus 6.0.5 API/CLI
contracts are checked against its [tagged source](https://github.com/lxc/incus/tree/v6.0.5).
Storage and firewall behavior follow the official
[btrfs](https://linuxcontainers.org/incus/docs/main/reference/storage_btrfs/) and
[firewall coexistence](https://linuxcontainers.org/incus/docs/main/howto/network_bridge_firewalld/)
references. Validate them on the pinned host before treating the pilot as usable.
