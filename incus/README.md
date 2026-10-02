# Incus preparation

This directory supplies the host foundation and generic Ubuntu 26.04 personal
boxes. The infrastructure owns OS provisioning, Tailscale/SSH connectivity,
source transport and HDD backups. Developers own all applications inside their
unprivileged boxes and have root there. Their agents work from laptops.

Read [Bare images and lifecycle](IMAGES.md) for image build, two-instance
acceptance, box creation/recreation and backups. Read [Developer-agent setup](DEVELOPER.md)
for repositories, Mutagen pairing, private configuration and daily use. The
[chart-box skill](../skills/chart-box/SKILL.md) guides laptop agents during remote
work. Legacy `APPS.md`, `BACKING.md` and `SOURCE-SYNC.md` apply only to Sean's
retained managed pilot, not personal-box onboarding.

`prep.py` prepares the host and checks storage/network identity. Its base-image
`box-create`/`box-provision` path remains for the existing pilot. Personal-box
creation uses `boxes.py` with an explicitly verified bare-image fingerprint.
Host mutations need operator sudo; developers receive only box-root SSH.
No CPU/memory caps or reservations are configured. Instances use
`boot.autostart=false`. Stopping retains their files; application startup inside
a running personal box belongs to its developer.

No image command fetches repositories, selects backend versions, installs npm
packages, scans repository secrets or enforces application egress. Personal boxes
have normal public-network egress subject to the host boundary below. Shared k3s
capture restrictions remain independent. No existing pilot, data or sync session
is automatically migrated or retired.

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

## 3. Build the bare image and create boxes

Follow [IMAGES.md](IMAGES.md). The generic builder uses the verified Ubuntu and
Tailscale artifacts above plus pinned nvm/tooling inputs. It publishes a private
candidate and creates two test instances with independent HDD/SSH/Tailscale
identities. Acceptance yields an immutable fingerprint for `boxes.py create`.
No application stack or source seed is included.

Provisioning uses the developer's selected public key and the host timezone.
Enrollment uses that developer's account in the existing team tailnet. Retained
data without a matching instance is refused by ordinary creation; planned
recreation uses explicit backup, old-rootfs retention and HDD identity adoption.

| Inside the box | Storage |
| --- | --- |
| `/srv/chart/source/<repo>` | SSD source mirror |
| nvm, node_modules, package caches, Docker data | SSD rootfs |
| `/srv/chart/data` | Required retained per-box HDD attachment |
| `/srv/chart/data/identity` | Retained SSH host keys and Tailscale state |
| Host `shared-dev/backups/boxes/<stamp>/<box>` | Nightly HDD copies and explicit rootfs exports |

APT upgrades remain explicit. Guest package timers are disabled. Incus preparation
never recursively rewrites host HDD ownership. Check idmapped access and retained
identity before treating recreation as accepted.

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

Give the laptop agent the box name, trusted SSH host-key fingerprint,
[developer guide](DEVELOPER.md), and `skills/chart-box/` with its helper scripts.
Discover checkout paths; pairing uses root SSH and `/srv/chart/source/<repo>`.
There is no daemon in the box beyond Mutagen's SSH-started agent, no Syncthing
pairing, and no application-side activation helper.

The helper records owned sessions and Git-tracked private-pattern exceptions in
`~/.config/chart-box/box.json`. It checks overlaps, flushes and verifies content,
and provides explicit policy refresh. Preserve existing pilot sessions until the
laptop agent deliberately migrates them; their source layout/protocol differs.
An operator who needs box SSH must have an explicitly authorized public key;
the developer's laptop key does not authorize the PC agent.

## Capacity and retention

Run `status` after image/package preparation, dependency installs, snapshots or
new boxes. Record pool consumption, btrfs data/metadata allocation, host SSD
free space, and later per-box `docker system df -v` and pnpm-store usage.
Investigate at 70% pool usage, below 25% host SSD free, or unexpected growth at
any level. Pause optional heavy work at 85% pool usage or earlier btrfs
allocation pressure. Preparation is conservative and refuses at the review
threshold. There is no background monitoring or automatic cleanup.

An Incus snapshot excludes the attached HDD data. The scheduled HDD copy stops the box; application-aware dumps are a developer
choice. Restoring a rootfs snapshot requires coordinated source sync and review
of the developer's application/data compatibility. Never clone enrolled identities into
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
python3 tests/bare_box.py
python3 -m py_compile incus/prep.py incus/firewall.py incus/runtime-proof.py
bash -n incus/guest-prepare.sh
```

These are offline checks, not live deployment acceptance. Incus 6.0.5 API/CLI
contracts are checked against its [tagged source](https://github.com/lxc/incus/tree/v6.0.5).
Storage and firewall behavior follow the official
[btrfs](https://linuxcontainers.org/incus/docs/main/reference/storage_btrfs/) and
[firewall coexistence](https://linuxcontainers.org/incus/docs/main/howto/network_bridge_firewalld/)
references. Validate them on the pinned host before treating the pilot as usable.
