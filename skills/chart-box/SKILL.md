---
name: chart-box
description: Develop and operate projects in a personal chart-infra box through laptop-owned source, Mutagen and SSH. Use for repository pairing, remote builds/tests, service startup/logs, hot reload, sync diagnosis and project databases. Any repository or application stack is supported; read the laptop mapping before choosing paths, ports or commands. Exclude unrelated SSH/sync work and host administration.
---

# Personal chart box

Codex and Claude Code use the same helper and SSH commands. The coding agent and
source checkouts live on the laptop. Mutagen mirrors selected
repositories into the personal box; SSH runs remote commands. The developer owns
applications inside the box; host/image/recovery work belongs to the operator.
Boxes can host any developer-selected repositories and services. Discover each
project's language, tooling and dependencies from its own instructions; Node,
Mongo and chart-backend examples in the guide are optional application patterns.

## Select the target

Use the user's explicit mapping, otherwise inspect regular
`~/.config/chart-box/*.json` files and match the box/repository. Resolve ambiguity
before acting; do not create a new mapping just because `box.json` is absent.
Mappings contain `box`, `ssh`, `source_root`, `repos` and `sessions`. Discover
absolute checkout paths and pass the selected `--config` on every helper call.
Config symlinks are refused. Preserve unrelated Mutagen/Syncthing sessions.

Read [developer setup and daily use](../../incus/DEVELOPER.md) for pairing,
exclusions, private env/key import, runtimes, databases, dump adoption and recovery.
Keep this skill with its `scripts/sync.py`, `policy.py` and linked guide. The helper
requires Python 3.11+ and Mutagen 0.18.1 on the laptop.

## Work from the laptop

Edit mirrored source only on the laptop. Private configuration and generated
Linux dependencies may be maintained inside the box. Ordinary new source files
sync without a commit; tracked private-pattern files are not content-scanned.
Pause before changing their tracked status, then refresh/resume/flush according
to the developer guide. Never switch to one-way-replica to erase a conflict.

Before remote tests, startup or inspection depending on edits, flush each affected
repository through the helper and require matching fingerprints:

```sh
python3 <skill-directory>/scripts/sync.py flush --config <mapping> --repo <repo>
ssh <recorded-ssh-target> '<project-specific command>'
```

A raw Mutagen flush does not prove matching content. Cross-repository flushes are
not an atomic snapshot and do not prove application readiness. Use each repository's
instructions and record the target and actual command. For Node projects, load
`/opt/nvm/nvm.sh` explicitly in noninteractive SSH and select project-required
runtime versions.

## Operate and diagnose

Discover the developer's supervisor, paths, ports and log locations. Use its
actual restart/test commands; no universal application CLI or service names are
provided. Use the project's configured endpoints; the chart API example uses
`http://<box>:3000`. Database credentials stay private; the guide includes Mongo
Compass and dump-import examples.
Never infer permission to drop data from a request to import a dump.

For missing edits, inspect owned-session status, pause/connectivity/errors and
conflicts first; then flush and compare content before inspecting watcher logs.
Do not delete the remote tree to force convergence. Use helper setup/status/pause/
resume/flush/refresh for ordinary session management.

A stopped box or unavailable host storage goes to the operator. Recreation keeps
HDD data/identity but clears rootfs source/dependencies. Coordinate paused sync
and application restoration. Nightly coverage is the data attachment only;
developers own startup behavior after a box restart.
