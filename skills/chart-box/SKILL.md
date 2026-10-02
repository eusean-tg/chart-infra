---
name: chart-box
description: Work on chart backends in a developer's remote personal box, including auth-service-backend, tharamine-user-service, orange-v2-backend and their databases. Use for backend tests, process startup/restarts/logs, checking hot reload, missing edits, Mongo/Dragonfly connections, dump import/export, adding repositories to sync, and SSH/Mutagen operations for these boxes. Read the laptop mapping before assuming where code, ports, logs or data live; exclude unrelated SSH and synchronization work.
---

# Personal chart box

The laptop owns the source and runs the coding agent. The personal Incus box runs
projects, tests and databases. Mutagen mirrors each registered checkout one way
into `/srv/chart/source/<repo>`. The developer is root in the box and owns its
applications. Host/image administration belongs to the operator.

## Establish the target

Read `~/.config/chart-box/box.json` on the laptop. It records `box`, `ssh`,
`source_root`, `repos` (absolute laptop path to destination name) and `sessions`
(destination name to owned session ID). Use its explicit `--config` if the
user selected another box. Do not invent a mapping when the file is absent;
follow the [developer guide](../../incus/DEVELOPER.md) with operator-supplied SSH
host-key fingerprint and developer-selected checkout paths.

This skill's `scripts/sync.py` is the laptop helper. Keep its sibling `policy.py`.
Invoke it with Python 3.11+ and Mutagen 0.18.1. The operator's existing managed
pilot may use a different helper/config: do not migrate or terminate those
sessions implicitly. Locate the intended deployment before using either protocol.

## Flush before remote work

Before any remote test, startup or inspection that depends on edits, flush the
relevant session through the helper and require success, then run over recorded
SSH. For a recorded Orange checkout, adapt the repository's actual test command:

```sh
python3 <skill-directory>/scripts/sync.py flush --repo orange-v2-backend
ssh <recorded-ssh-target> 'bash -lc "cd /srv/chart/source/orange-v2-backend; . /opt/nvm/nvm.sh; nvm use; pnpm test"'
```

For cross-repository changes, flush each affected repo before executing dependent
tests. Flushing verifies included content; it does not make several repositories
an atomic update or wait for application readiness. Inspect the application's
ready signal separately. Record the box and command used in test results.

## Run and debug projects

Read the repositories' instructions and package scripts. Discover the developer's
supervisor, service names, logs, ports and working directories over SSH. Do not
assume Docker, a universal box CLI or systemd unit exists for an application.
For a recorded unit, use `journalctl -u <unit>` and `systemctl restart <unit>`;
for a developer-owned Compose project, use its actual `docker compose logs` and
restart commands. Adding another database/service is developer work inside the
box; it does not require an infrastructure application feature.

Load `/opt/nvm/nvm.sh` explicitly in noninteractive commands; select the version
required by `.nvmrc` or repository guidance. Install Linux dependencies in the box.
Reinstall after lockfile/tool changes as the repository requires. Source and
node_modules are on SSD; durable data belongs under `/srv/chart/data` on HDD.

Compass/mongosh can use `<box>:27017` when the developer publishes Mongo there.
Use `directConnection=true`, the actual authentication database and privately
supplied credentials. Never print connection passwords. For dump adoption,
inspect versions/namespaces, preserve the original archive, copy over SCP and
restore using the [agent procedure](../../incus/DEVELOPER.md#6-import-an-existing-mongo-dump).
Do not infer permission to drop existing data from an import request.

The pilot API convention is `http://<box>:3000`; discover project overrides. Vite
can stay on the laptop. Box timezone follows the host; inspect it rather than
assuming UTC. Box egress is normal Internet access with developer-owned keys.
Shared k3s capture restrictions do not govern personal-box application work.

## Source and private files

Edit mirrored source only on the laptop; there is no Git checkout in the box.
Create/edit private config and generated dependencies in the box as needed.
New ordinary source files sync without staging or committing.
`.git`, node_modules, build output and caches stay excluded. Untracked secret-pattern
files are excluded, except sample/example templates. Tracked files are included
without content inspection. Do not assume a tracked credential stays laptop-local.

Use helper `setup`, `status`, `pause`, `resume`, `flush` and `refresh`; do not create,
terminate or reconfigure its sessions manually. Default one-way-safe preserves
conflicting remote edits. Preserve unrelated Mutagen/Syncthing sessions.

Before changing tracked status of private-pattern paths or switching a branch
that changes them, pause the session and run `refresh`, then resume/flush. Ignore
rules are snapshots, not a live Git-index filter. The helper detects mismatch on
resume/flush and pauses; it cannot retroactively undo transfer through an old
tracked exception. Ordinary source edits do not need refresh.

## When something is wrong

For "my change isn't showing," run `status` first. Check paused/disconnected state,
conflicts and policy-refresh requirements; resolve those before watcher debugging.
A failed flush or fingerprint mismatch is not a passed test prerequisite. Preserve
both sides of conflicts; never use one-way-replica or delete the remote tree to
force convergence. After flush succeeds, inspect the process's actual source path,
watcher logs and readiness.

A stopped box or host-storage failure goes to the operator. Recreation preserves
HDD identity/data but gives an empty SSD source/dependency environment. Coordinate
paused sessions and reinstall tooling/config rather than assuming source is still
there. Nightly backups cover only HDD data and stop the box briefly; manually
started apps need the developer's own startup policy. Follow the developer guide
for first setup and the operator for host/image/recovery work.
