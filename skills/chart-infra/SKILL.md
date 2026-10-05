---
name: chart-infra
description: Operate the chart-infra host foundation, generic Incus image, personal-box lifecycle and HDD backups. Use chart-box for developer application work and laptop-to-box source synchronization. Exclude the separate Go pipeline and production.
---

# Chart infrastructure operator

Establish host, checkout, backend and intended operation. Read AGENTS.md and
README.md. Keep host Incus/Docker/Kubernetes administration with the operator;
developers are root only in their personal boxes. Preserve unrelated workloads.

## Personal boxes

Read `incus/README.md` and `incus/IMAGES.md`. Use `prep.py` for host setup/status,
`image.py` for generic image build/acceptance, `boxes.py` for explicit creation or
recreation, and `backup.py` for backups/restore checks/timer installation. Review
plans before `--apply`; host mutations require operator sudo.

The bare image contains generic OS/development tools and an unenrolled identity.
It contains no application source, service images, source pins or app credentials.
Application setup, versions, dependencies, private config, data and startup are
developer responsibilities. Personal boxes have normal egress; the shared k3s
capture restrictions do not apply to them. Follow application instructions instead
of adding another infrastructure app guard or CLI.

Use `skills/chart-box/SKILL.md` and `incus/DEVELOPER.md` for laptop agents. Discover
paths and use their recorded box mapping. Do not operate laptop sessions from host
assumptions or run application fixtures as bare-box acceptance.

Follow registered root-pool placement for source/dependencies and incus/STORAGE.md
for pool migration. Keep the required verified HDD data attachment, isolated UID
maps, host firewall, inotify settings and host-matched timezone. No CPU/memory
limits or reservations. The LAN UDP exception supports Tailscale transport;
tailnet ACLs govern overlay peer access. Separate IPs alone do not enforce ACLs.

Before recreation, coordinate paused source sessions, create an independent HDD
backup and rootfs export, prove scratch restore, and retain the old stopped rootfs.
Reuse HDD SSH/Tailscale identities and both intended authorized keys. Do not start
two enrolled identity copies. Inspect a partial recreation's phase record before
recovery; never initialize replacement data to bypass a failed mount/marker.

Nightly HDD policy: 04:00 Asia/Kuala_Lumpur, seven days of completed nightly copies,
stored under `/var/backups/chart-incus` on NVMe with the 25% reserve, brief box stop
and restoration of prior box running/stopped state. Enroll each accepted personal
box explicitly; managed-pilot, image-test, builder and retained instances cannot
join automatic backups. Prove recovery with `tests/bare_recovery_live.py` on the
verified build's test box before migration. Retire test instances and tailnet device
registrations after acceptance; retain HDD data and evidence. Developers own
app startup after boot. Manual backups, rootfs exports and incomplete generations
are retained. No other pruning/deletion is authorized by routine setup.

Retained k3s deployments and historical pilot data are outside these tools.
Preserve them unless the operator explicitly authorizes a separate retirement.

## Report and document

Record tools implemented, versions, actual deployment, automated versus client
acceptance and remaining work separately. A written tool is not a verified live
migration. Put reusable procedures in the repo and host-specific evidence/roadmap
in `/home/sean/obsidian/vault/Chart Infra/`, following its index conventions.
