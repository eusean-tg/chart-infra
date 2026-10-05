# Chart infrastructure

Chart-infra supplies personal Ubuntu 26.04 Incus boxes, Tailscale connectivity,
laptop-to-box Mutagen sync and retained HDD backup tools. Developers edit
repositories and run agents on laptops; their agents configure and operate
applications as root inside their own unprivileged boxes. Source and dependencies
use the box’s registered root pool; retained data belongs under `/srv/chart/data`
on HDD. Backups remain on SSD/NVMe.

The bare image contains generic development tools. Application versions,
environment files, dependencies, startup and data belong to each developer.
Host administration belongs to the operator.

## Start here

| Task | Guide |
| --- | --- |
| Understand the whole setup: instances, networking, storage, volumes and backups | [Architecture and storage map](incus/ARCHITECTURE.md) |
| Prepare the Ubuntu 26.04 host, networking and storage | [Incus preparation](incus/README.md) |
| Test HDD-backed box storage before migration | [Storage trial](incus/STORAGE.md) |
| Build and accept an image, create/recreate boxes, schedule backups | [Image and lifecycle](incus/IMAGES.md) |
| Set up projects, private configuration and databases | [Developer-agent guide](incus/DEVELOPER.md) |
| Sync source, run remote tests and inspect applications | [Chart-box skill](skills/chart-box/SKILL.md) |
| Operate the host, images and recovery tools | [Chart-infra skill](skills/chart-infra/SKILL.md) |
| Find deployed endpoints, evidence, retained artifacts and remaining work | [Chart Infra knowledge base](/home/sean/obsidian/vault/Chart%20Infra/INDEX.md) |

Use `incus/prep.py` for host preparation/status, `incus/image.py` for image
build/acceptance, `incus/boxes.py` for creation/recreation, and `incus/backup.py`
for backup enrollment, copies and restore checks. Read the linked procedures and
review the plan before `--apply`. Host mutations require operator sudo.

The repository owns reusable code and operating instructions. The Obsidian
knowledge base owns installation-specific facts, investigations, evidence and
plans. Retained k3s deployments, historical pilot data, the Go pipeline and
unrelated host services are outside these tools. Preserve their resources.

## Development checks

These standard-library checks use local fixtures without deploying workloads:

```sh
python3 tests/incus_prep.py
python3 tests/bare_box.py
python3 tests/bare_boundary.py
python3 tests/storage_trial.py
python3 tests/storage_move.py
python3 tests/storage_migrate.py
python3 tests/syslog.py
bash -n incus/bare-base.sh incus/bare-identity.sh incus/guest-firewall.sh
```

The `tests/*_live.py` checks contact machines or create fixtures. Read
[image acceptance](incus/IMAGES.md) and the selected test before running one;
never substitute a developer's working box for a disposable test target.
Read [AGENTS.md](AGENTS.md) before modifying this project.
