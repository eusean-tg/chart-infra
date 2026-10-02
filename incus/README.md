# Incus preparation

This directory prepares an Ubuntu 26.04 host foundation and an unprivileged Ubuntu
26.04 developer box. The existing `./chart` commands operate k3s profiles; they do
not operate these boxes. Mutagen over SSH is the box source transport.

`prep.py` uses Python's standard library. Preparation requires a host operator
with sudo; developers receive root inside their own box only. Do not grant host
Incus, Docker or Kubernetes access to enable laptop operations.

## Scope

| Command | Effect |
| --- | --- |
| `inspect` | Read host identity, mount, route and capacity information |
| `fetch` | Download and checksum public Ubuntu image and Tailscale artifacts into an explicit cache directory |
| `host-prepare` | Plan by default; `--apply` installs pinned host packages, creates the 200 GiB SSD btrfs pool, project and NAT bridge, and installs bridge-scoped firewall rules |
| `box-create` | Plan by default; `--apply` imports the pinned image and creates a **stopped** box with an isolated UID map, TUN and its own HDD attachment |
| `box-provision` | Plan by default; `--apply` starts that box and installs Docker/Compose, unenrolled Tailscale, key-only SSH and the guest input firewall |
| `status` | Read capacity and image/instance inventory; with sudo, include pool and btrfs allocation details |

No command fetches application source, imports environment files, creates Mongo
data, starts applications, enrolls Tailscale, changes laptop sync sessions or
deletes retained storage. There is no teardown/prune command. No CPU/memory limits
are configured. Instances have `boot.autostart=false`.

These are preparation commands, not an accepted deployment. Live nested-Docker,
firewall, storage and restart checks are required on the target host. A sanitized
golden-image publisher, Compose stack, dataset switching and box-aware Mutagen
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
Docker-coexistence accepts. It denies box underlay access to private networks,
the host management plane, WARP and the host tailnet interface. IPv6 underlay is
disabled; IPv6 inside the Tailscale tunnel remains independent. UDP 41641 is the
only unsolicited underlay box ingress allowance. NAT Internet access permits
package preparation; it is **not** application offline-egress enforcement.

The root-owned `chart-incus-firewall.service` reapplies after Docker/Incus
restarts. It never flushes their tables or shared chains. Inspect effective
rules and test coexistence; another daemon or workstation firewall can still
block traffic. LAN peer connections may use Tailscale relays because private
underlay destinations are blocked. Do not broaden LAN access to bypass a test.

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

| Inside the box | Storage |
| --- | --- |
| `/srv/chart/source/<workspace>/<repo>` | SSD source mirror |
| `/srv/chart/cache/pnpm` | Independent SSD package store |
| `/var/lib/docker`, `/var/lib/containerd` | SSD images, runtime state and dependency volumes |
| `/srv/chart/data/datasets/<dataset>/mongo/member-0` | Retained HDD database path; created by future explicit dataset initialization |
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
3. Pull an explicitly pinned public smoke-test image. Prove Docker pull, a
   disposable build, container execution and named-volume write/read after
   recreation. Preserve the test volume; no `prune` or `down -v`. A successful
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
6. From the laptop, verify the SSH host-key fingerprint through the host
   operator, then connect as `root@<box>`. This is root inside the unprivileged
   box. Do not disable host-key checking. Verify service access from a teammate
   during application acceptance; successful enrollment is not that proof.

## Mutagen handoff

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

## Development checks

```sh
python3 tests/incus_prep.py
python3 -m py_compile incus/prep.py incus/firewall.py
bash -n incus/guest-prepare.sh
```

These are offline checks, not live deployment acceptance. Incus 6.0.5 API/CLI
contracts are checked against its [tagged source](https://github.com/lxc/incus/tree/v6.0.5).
Storage and firewall behavior follow the official
[btrfs](https://linuxcontainers.org/incus/docs/main/reference/storage_btrfs/) and
[firewall coexistence](https://linuxcontainers.org/incus/docs/main/howto/network_bridge_firewalld/)
references. Validate them on the pinned host before treating the pilot as usable.
