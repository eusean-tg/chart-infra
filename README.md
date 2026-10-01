# Chart infrastructure

Standalone development infrastructure for the chart frontend and its TypeScript backends. The Go pipeline remains in meta_repo.

Every Mongo profile uses **one data-bearing member, initialized as replica set `rs0`**, with keyfile authentication and a private database user. Laptops use a Tailscale IP and port with `directConnection=true`. In-cluster applications use cluster DNS and `replicaSet=rs0`. Mongo needs no TLS, CA, SRV records, split horizons or laptop resolver file.

Sean’s offline frontend-backend pilot is running at **http://100.66.127.115:13000**, with Mongo at **100.66.127.115:27018**. Start with the [human setup and daily-use guide](docs/GETTING-STARTED.md). The earlier Mongo-only pilot remains at port 27017; see [its Compass walkthrough](docs/MONGO-PILOT.md).

For source synchronization, agents should follow [AGENT-ONBOARDING.md](docs/AGENT-ONBOARDING.md). It uses explicit repository paths and granular commands. Mutagen over existing SSH replaces the dedicated Syncthing runtime and listener. Its former identity/state/volumes remain retained. Sean’s Mac sessions are registered and clean source has been activated; the laptop agent reports all three real backend watchers passed. The later handoff records real Tailscale recovery and Sean-reported frontend sign-in/save/reload success. Existing OpenScape sync is unchanged.

## Application commands

```sh
./chart mongo up --profile sean
./chart apps up --profile sean
./chart apps status --profile sean
./chart apps down --profile sean
./chart apps down --profile sean --data-only
```

The app runner manages auth, tharamine, Orange and a private Dragonfly cache. Generated development identities/config live on the HDD and are mounted read-only; no production dotenv files or dotenvx decryption keys are used. Apps run `tsx watch` against isolated source mounts. The [operator preparation steps](docs/PREPARATION.md) cover pinned clones, toolchain builds, private npm dependencies and the synthetic role fixture. `up` never fetches source, installs packages or runs migrations.

## Mongo: start, stop and retain data

On the PC:

```sh
cd ~/workspace/chart-infra
./chart mongo status
./chart mongo up
./chart mongo down
./chart mongo down --data-only
./chart mongo up
```

The default profile is `mongo-pilot-single`. `down` withdraws its external route and scales Mongo to zero. `down --data-only` also removes its runtime controller, Services, ConfigMap, keyfile Secret and NetworkPolicy. Both preserve its HDD data, identity, inventory, namespace, storage class and PV/PVC. There is no data-delete command.

Provision another profile explicitly:

```sh
./chart mongo prepare --profile sean
./chart mongo up --profile sean
./chart mongo status --profile sean
./chart mongo down --profile sean --data-only
```

Default port assignments: pilot 27017, Sean 27018, Alex 27019, Ryan 27020, Gerald 27021. These are defaults, not claims those developers’ Mongo services are deployed. For another profile, specify an unused `--port` during `prepare`. Port reservations persist through teardown. Existing profiles retain their original identity and port; prepare refuses an implicit port change.

`prepare` creates a new empty `default/member-0` dataset once. `up` verifies the registered host, HDD UUID, volume UIDs and data markers. It never silently creates replacement data, fetches source or reconfigures an existing replica set. The ordinal directory layout supports adding members later through an explicit design and migration; the current runner enforces one member.

## Where files live

- Source: this directory. Python standard library only; no runtime imports from meta_repo.
- Host registration/evidence: `~/.local/state/chart-infra/`.
- Profile exports: `~/.local/state/chart-infra/profiles/<profile>/compass-uri.txt` and `internal-uri.txt`. These contain private credentials; do not put them in source sync or docs.
- Backend source and dependencies: `/mnt/hdd/shared-dev/profiles/<profile>/source/` and `dependencies/`. Old SSD copies/volumes remain retained for rollback.
- Retired source Syncthing identity/config: `~/.local/share/chart-infra/sync/<profile>/`, separate from source and Mongo.
- Mongo files: `/mnt/hdd/shared-dev/profiles/<profile>/mongo/default/member-0/`.
- Retained keyfile/passwords/inventory: `/mnt/hdd/shared-dev/profiles/<profile>/identity/`, outside the Mongo PV.
- Plain TCP routes: `access.py`; generated config and route inventory are under `~/.local/state/chart-infra/access*`.
- [Deployment record](docs/DEPLOYMENT.md): exact versions, applied-manifest locations and verification results.
- `docs/design/`: historical Obsidian design snapshots; current decisions are in the consolidated vault note and this runbook. The original Fable review and `retired/` are historical, superseded where the 2026-10-01 decisions disagree.

The separate `chart-infra-access` Docker container follows the existing TimescaleDB/Dragonfly IP:port access pattern. It binds only the Tailscale IP and rejects non-Tailscale sources. It does not alter the existing `shared-dev-access` proxy. Changes to the chart route set briefly recreate only the chart proxy; Mongo clients may reconnect. Configuration is validated first, with the previous proxy retained for rollback until the replacement starts.

## Verify

```sh
./chart mongo check
python3 tests/storage_guards.py
```

These checks write synthetic fixture records only in the selected profile’s `chart` database. They verify direct IP access, in-cluster discovery, transactions, change streams, authentication, storage and network boundaries. There is no secondary or failover test with one member.

`tests/profile_isolation.py` tests the pilot against `mongo-isolation-check`. That second synthetic profile was verified and then stopped with `--data-only`; its data remains retained. Restart it explicitly before repeating the isolation test. Its port is 27022. Actual developer Linux-account/RBAC provisioning is a later step.

## Scope and prerequisites

Installed: reusable Mongo lifecycle, explicit source/dependency/toolchain preparation, and Sean’s offline auth/tharamine/Orange pilot with login, refresh, saved workspace retention and hot reload verified. Two Mongo profiles passed isolation checks; a second full app profile has not been deployed. Named-dataset switching and developer accounts remain separate work. Explicit source/dependency selection and PC-side sync are installed; Mac setup/source activation passed, with actual Mac-to-backend hot reload reported successful by the laptop agent. Only the `default` dataset is implemented.

Prerequisites: Python 3, kubectl, Docker, findmnt and Tailscale. Images are pinned by digest. Mongo alone needs no private package authentication. Application preparation needs GitLab SSH and npm read access in a mode-0600 `~/.npmrc`; see the preparation guide. No CPU/memory requests or limits are configured for Mongo or the chart proxy. The 0.5 GiB WiredTiger cache flag is application tuning; PV capacity 50Gi is metadata, not preallocation or an enforced directory quota.

The old DNS/TLS/SNI pilot is retired. Sean explicitly authorized deleting its unused synthetic directory; it and its old PVC/PV were removed after all consumers stopped. API-hostname DNS is a separate decision. No vendor collectors, metadata refresh, history downloads or production actions are part of these commands.

## Local source control and SSH execution

This directory is now a standalone local Git repository on `main`. Source, reviewed patches, tests and documentation form the staged initial baseline; the first commit is pending review. No remote is configured and nothing has been pushed. Inspect with `git status` and `git diff --cached`. Runtime state, kubeconfigs, private env/key files, connection URIs, dependency trees and generated outputs belong outside Git and are excluded by `.gitignore`.

Commands do not depend on interactive shell exports. If `KUBECONFIG` is unset, `common.py` defaults it to the invoking user's `~/.kube/config` for all child processes; explicit overrides remain intact. The registered-cluster guard still rejects a different cluster. No permissions on k3s' root-only server config were changed. Sync status/export are guarded reads without lifecycle locks or the mutation-only free-space check.
