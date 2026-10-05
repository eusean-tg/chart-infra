# Chart infrastructure

Personal Ubuntu development boxes with Tailscale access, laptop-to-box Mutagen
sync and retained HDD data. Developers own application setup; operators own the
host foundation and box lifecycle.

| Task | Read |
| --- | --- |
| Understand storage, networking, responsibility and backup coverage | [Architecture](incus/ARCHITECTURE.md) |
| Prepare the host, build images, create/recreate boxes and manage backups | [Operator guide](incus/README.md) |
| Set up repositories, private configuration, databases and daily development | [Developer-agent guide](incus/DEVELOPER.md) |
| Route host/image/recovery work | [Chart-infra skill](skills/chart-infra/SKILL.md) |
| Sync code and operate applications from a laptop agent | [Chart-box skill](skills/chart-box/SKILL.md) |
| Find deployed endpoints, recovery locations and open work | [Environment knowledge base](/home/sean/obsidian/vault/Chart%20Infra/INDEX.md) |

Read [AGENTS.md](AGENTS.md) before changes. This repository owns reusable code and
procedures. The environment knowledge base owns installation facts and open plans.

## Development checks

These standard-library checks use local fixtures without deploying workloads:

```sh
python3 tests/incus_prep.py
python3 tests/bare_box.py
python3 tests/bare_boundary.py
python3 tests/syslog.py
bash -n incus/bare-base.sh incus/bare-identity.sh incus/guest-firewall.sh
```

`tests/*_live.py` contacts machines or creates fixtures. Read the
[acceptance procedures](incus/README.md#test-image-recovery) and selected test before
execution. Never substitute a working developer box for a disposable fixture.
