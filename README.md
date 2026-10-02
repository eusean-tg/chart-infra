# Chart infrastructure

Chart-infra supplies personal Ubuntu 26.04 Incus boxes, Tailscale connectivity,
laptop-to-box Mutagen sync and retained HDD backup tools. Developers run their
agents and edit repositories on laptops; their agents configure and operate
applications as root inside their own unprivileged boxes. Source/dependencies use
the SSD pool; retained data belongs under `/srv/chart/data` on HDD.

The bare image contains generic development tools. It contains no chart source,
service images or app credentials. The operator does not prescribe application
versions, environment files, dependencies or startup/egress rules inside boxes.

The retained k3s runner and Sean's managed Incus application pilot remain available
for their existing deployments. Their application contracts are legacy references,
not the onboarding model for personal boxes.

## Start here

| Task | Guide |
| --- | --- |
| Prepare the Ubuntu 26.04 Incus host and personal boxes | [Incus preparation](incus/README.md) |
| Build a generic image, create/recreate boxes and schedule backups | [Bare image and lifecycle](incus/IMAGES.md) |
| Set up projects in a personal box | [Developer agent guide](incus/DEVELOPER.md) |
| Run remote tests, inspect logs or sync code | [Chart-box skill](skills/chart-box/SKILL.md) |
| Connect a frontend, edit code, inspect Mongo, start or stop a prepared profile | [Getting started](docs/GETTING-STARTED.md) |
| Configure source synchronization or import development environment settings with an agent | [Agent onboarding](docs/AGENT-ONBOARDING.md) |
| Prepare runtime/dependencies, inspect storage and understand commands | [Operations reference](docs/OPERATIONS.md) |
| Give another agent the workflow | [Chart-infra skill](skills/chart-infra/SKILL.md) |
| Find this PC's endpoints, verification evidence, investigations and remaining work | [Chart Infra knowledge base](/home/sean/obsidian/vault/Chart%20Infra/INDEX.md) |

The repository is the source of truth for reusable operating instructions and code. The Obsidian knowledge base holds installation-specific facts, reviews, rollout evidence and plans.

## Retained k3s lifecycle

Run from this checkout as the prepared profile's operator. Replace `your-profile` explicitly; command defaults differ between subsystems.

```sh
CHART_PROFILE=your-profile
./chart mongo up --profile "$CHART_PROFILE"
./chart apps up --profile "$CHART_PROFILE"
./chart apps status --profile "$CHART_PROFILE"
./chart apps down --profile "$CHART_PROFILE"
```

`up` uses prepared source, Linux dependencies, configuration and images. It does not fetch source, install packages or invoke migration jobs. Backend startup still executes the application's own initialization and worker code. `down --data-only` removes runtime objects and retains data, identities, source, caches and volumes. There is no data-delete command.

Mongo uses replica set `rs0`, one data-bearing member and keyfile authentication. Laptop clients use a Tailscale IP and profile port with `directConnection=true`; Pods use cluster DNS and `replicaSet=rs0`. Mongo requires no TLS certificate, SRV record or resolver configuration. Chart routes bind the registered Tailscale IP and restrict source addresses to the Tailscale range.

## Retained k3s implementation boundaries

- Backend source and Linux dependencies reside under `/mnt/hdd/shared-dev/profiles/<profile>/`. Private identities and runtime configuration stay outside source synchronization and Git.
- Workloads declare no CPU or memory requests or limits. Mongo's WiredTiger cache flag is application tuning; PV storage capacity is directory-backed volume metadata.
- Application deployment, source fetching, synchronization, dependency installation and any future live capture are separate operations. Live capture requires a cluster-enforced deadline.
- Host registration and developer Linux-account/RBAC provisioning have no automated bootstrap interface. The runner is bound to the registered single-node cluster and HDD identity; it is not a portable cluster installer.
- Only Mongo dataset `default` is implemented. Named-dataset creation/switching and a second complete application-profile isolation proof are tracked in the knowledge-base roadmap.
- The Go pipeline and other host services are outside this project's scope.

## Development checks

The standard-library unit checks run without deploying workloads:

```sh
python3 tests/sync_safety.py
python3 tests/kubeconfig.py
python3 tests/stock_source.py
python3 tests/backend_key.py
python3 tests/mongo_checks.py
python3 tests/skill_context.py
python3 tests/incus_prep.py
python3 tests/incus_backing.py
python3 tests/incus_apps.py
python3 tests/incus_sync.py
python3 tests/bare_box.py
```

See [verification boundaries](docs/OPERATIONS.md#verification) before running tests that contact the cluster or create fixtures. Read [AGENTS.md](AGENTS.md) before modifying this project.
