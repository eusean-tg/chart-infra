# Incus backing services

Legacy reference for Sean's existing managed pilot. Personal-box onboarding uses
[the developer guide](DEVELOPER.md); do not install this managed application
contract into a bare box. Preserve the running pilot until explicit migration.

`backing.py` operates Mongo and the chart stack's separate Dragonfly inside a
prepared unprivileged box. Run it as root **inside that box**, over its verified
SSH connection. It explicitly targets the box's local Docker socket. The host's
TSDB, Kafka, Go cache and existing k3s chart profiles remain independent.

Application backends use [Box applications](APPS.md). Box-aware source sync,
named-dataset switching and backup restoration require separate implementation.
These commands operate one explicitly prepared dataset per box. Registered app
writers must stop before backing lifecycle changes; remove their containers with
`box.py down` before backing `down`. Unknown network clients are refused. There
is no data deletion or pruning operation.

## Install and prepare

Before first deployment, the PC operator verifies the HDD UUID against the box
marker and the host preparation config. Inside the box, the helper verifies the
hostname, isolated UID map, required ext4 attachment at `/srv/chart/data`, its
per-box source path, retained marker and guest firewall. The guest does not expose
the filesystem UUID; its checks complement the host UUID check.

Copy `backing.py` and `backing_checks.py` from the reviewed checkout's `incus/`
directory into `/opt/chart-infra/incus/` inside the selected box. Keep both files
from the same revision. This installs code only. Preserve a prior installed
revision before an explicit update; startup does not update helper code.

Choose the box's actual Tailscale IPv4 and a fresh dataset name. Discover both
the developer's SSH key and checkout location rather than assuming laptop paths.
The commands below run inside the box:

```sh
CHART_BOX=$(hostname)
CHART_BOX_IP=$(tailscale ip -4)
CHART_BACKING=/opt/chart-infra/incus/backing.py

python3 "$CHART_BACKING" prepare --box "$CHART_BOX" \
  --dataset fixture-a --tailscale-ip "$CHART_BOX_IP"
python3 "$CHART_BACKING" prepare --box "$CHART_BOX" \
  --dataset fixture-a --tailscale-ip "$CHART_BOX_IP" --apply

python3 "$CHART_BACKING" fetch --box "$CHART_BOX" --apply
python3 "$CHART_BACKING" up --box "$CHART_BOX" --apply
python3 "$CHART_BACKING" status --box "$CHART_BOX"
```

Mutation commands print a plan unless `--apply` is supplied. `fetch` pulls only
the two public image digests pinned in `backing.py`; it is independent of startup.
`prepare` creates a new empty HDD dataset and unique credentials. It refuses an
existing unregistered path and preserves an already registered matching dataset.
An interrupted preparation retains its files for inspection.

Mongo is pinned to 7.0.43 and Dragonfly to 1.36.0. Verify reported versions after
pulling; neither startup nor teardown selects a newer image. There are no CPU or
memory limits or reservations. Mongo's WiredTiger cache is explicitly 0.5 GiB,
with a 256 MiB oplog. Dragonfly uses cache mode and one proactor thread; its own
memory/eviction behavior is separate from Docker resource limits.
Mongo's soft/hard open-file ceiling is 64000, following
[MongoDB's ulimit guidance](https://www.mongodb.com/docs/v8.0/reference/ulimit/).
This supports app collection/index initialization without reserving RAM.

## Storage and identities

| Inside the box | Contents |
| --- | --- |
| `/srv/chart/data/datasets/<dataset>/mongo/member-0` | Mongo database files on HDD |
| `/srv/chart/data/datasets/<dataset>/dragonfly` | Retained Dragonfly snapshots on HDD |
| `/srv/chart/data/identity/backing/credentials.json` | Unique admin, per-service and synthetic-fixture Mongo credentials; cache password |
| `/srv/chart/data/identity/backing/keyfile` | Retained Mongo replica-set keyfile |
| `/srv/chart/data/identity/backing/compose.json` | Generated Compose definition, with no embedded passwords |
| `/srv/chart/data/identity/backing/inventory.json` | Dataset identity, initialization phase and credential/configuration hashes |
| `/srv/chart/data/identity/backing/verification/` | Non-secret synthetic acceptance reports |
| `/var/lib/docker`, `/var/lib/containerd` | SSD image/container state |

Mongo and Dragonfly run as UID/GID 999 with read-only root filesystems, dropped
capabilities and no privilege escalation. Only their dataset directories are
writable HDD mounts. Preparation assigns ownership only to paths it creates.
Credentials remain outside source synchronization, Git and image layers. Do not
paste them into chat or use the admin account in applications.

Mongo users authenticate against `admin`. `auth` can read/write database `auth`,
`tharamine` database `orange`, and `orange` database `orangeV2`. The fixture user
can access only `chart_fixture`. Bootstrap initializes `rs0` with member
`mongo:27017` through Mongo's localhost exception, then creates users. It never
force-reconfigures a set or rotates credentials on restart.

An initialized dataset must retain its marker and `member-0/WiredTiger`.
Missing data fails startup; it does not trigger a replacement database. Inspect
an interrupted `initializing` phase before retrying: `up` can finish the same
prepared identity, but never creates another identity to overcome an auth error.

## Connectivity

Both containers attach only to the internal Docker network
`chart-backing-runtime`. Internal clients use `mongo:27017` with `replicaSet=rs0`
and authenticated `redis:6379`. There are no Docker-published database ports.

`up` installs an on-demand systemd socket at the box's Tailscale IPv4, port 27017,
bound to `tailscale0`. `systemd-socket-proxyd` forwards to Mongo's internal address
as a dedicated dynamic user. The socket/service require the HDD mount and are not enabled at boot.
This plain TCP forwarding keeps Mongo on its internal-only network. The helper
refreshes the target after container recreation and refuses edited/foreign units.
The guest input firewall and tailnet access rules remain part of the boundary.

A laptop Mongo URI uses:

```text
mongodb://<service-user>:<password>@<box-tailscale-ip>:27017/<service-db>?authSource=admin&directConnection=true
```

Use SSH to obtain only the required credential from the protected JSON file;
transfer it privately into Compass or local configuration. Mongo requires no TLS
certificate, resolver entry or SRV lookup. Dragonfly is not published to laptops.
The plain Mongo route is encrypted in transit by Tailscale.

The backing network has no external route. Registered apps use this same network;
Orange has its own Tailscale-bound forwarder described in [APPS.md](APPS.md). Do not
attach an Internet-capable network to these database containers to publish a port.

## Daily lifecycle

```sh
python3 /opt/chart-infra/incus/backing.py up --box "$(hostname)" --apply
python3 /opt/chart-infra/incus/backing.py status --box "$(hostname)"
python3 /opt/chart-infra/incus/backing.py stop --box "$(hostname)" --apply
```

`stop` saves a Dragonfly snapshot, stops the Mongo listener and stops containers.
Stop app writers with `box.py stop` first. Backing `down` also requires
`box.py down` so app endpoints do not retain the owned network.
It fails before teardown if the cache snapshot cannot be saved. `up` waits for
authenticated readiness and health checks, then opens the Mongo listener. It
pulls no images, fetches no source and runs no application migrations or collectors.
Docker restart policies are `no`; these are on-demand services.

To remove containers and the owned Docker network while keeping the HDD data,
identities, snapshots and configuration:

```sh
python3 /opt/chart-infra/incus/backing.py down --box "$(hostname)" --apply
python3 /opt/chart-infra/incus/backing.py up --box "$(hostname)" --apply
```

Use this helper rather than raw Compose teardown to preserve the cache snapshot
step. Never use `down -v`, Docker/Incus prune or dataset deletion as teardown.
An abrupt crash can lose unsnapshotted cache writes; snapshots here provide orderly
cache restart retention, not database durability or a backup strategy.

## Acceptance checks

The check helper writes retained synthetic records into the fixture database and
one `chart_infra_verification` collection per app database. It exercises majority
transactions, change streams, per-user scope, unauthenticated denial, internal
replica discovery, cache auth, health checks and resource settings. Its controlled
network probe targets a short-lived listener inside the box, not an external API.

```sh
python3 /opt/chart-infra/incus/backing_checks.py --box "$(hostname)" --apply
python3 /opt/chart-infra/incus/backing.py stop --box "$(hostname)" --apply
python3 /opt/chart-infra/incus/backing.py up --box "$(hostname)" --apply
python3 /opt/chart-infra/incus/backing_checks.py --box "$(hostname)" \
  --expect-existing --apply
```

`--expect-existing` refuses missing Mongo/cache persistence tokens and never
recreates them. Repeat it after `down`/`up`. Each run updates the synthetic
transaction and appends a change-stream record; there is no cleanup step.

Separately verify authenticated `directConnection=true` access from another
tailnet machine, denial to the box's bridge address, and absence of an external
cache listener. These checks do not establish Mac Compass, browser acceptance,
whole-box restart, backup restore or second-box isolation. Record those separately
in the operator's knowledge base. A golden image must exclude the per-box Mongo
socket/service configuration, private identities and datasets.

Offline guard tests, from the checkout:

```sh
python3 tests/incus_backing.py
```
