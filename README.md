# Chart infrastructure

Chart-infra runs the chart application's TypeScript backends on a registered shared development PC. Developers run the frontend and their coding agent on a laptop; Mutagen sends backend source over SSH to HDD mirrors mounted by k3s.

The parallel [Incus pilot preparation](incus/README.md) provides host and Ubuntu 26.04 box preparation commands. Its source/dependencies use a bounded SSD pool and retained data uses HDD. The k3s lifecycle below remains independent; preparing a box does not migrate applications or change laptop sync sessions.

Each application profile contains auth, Tharamine, Orange, an authenticated Dragonfly cache, and a single-member MongoDB replica set. The environment supports offline sign-in and saved workspaces. Live market data, external delivery, billing and production integrations are outside its operating scope.

## Start here

| Task | Guide |
| --- | --- |
| Prepare the parallel Ubuntu 26.04 Incus host/box foundation | [Incus preparation](incus/README.md) |
| Run Mongo and Dragonfly in a prepared Incus box | [Incus backing services](incus/BACKING.md) |
| Prepare and run auth, Tharamine and Orange in an Incus box | [Box applications](incus/APPS.md) |
| Connect a frontend, edit code, inspect Mongo, start or stop a prepared profile | [Getting started](docs/GETTING-STARTED.md) |
| Configure source synchronization or import development environment settings with an agent | [Agent onboarding](docs/AGENT-ONBOARDING.md) |
| Prepare runtime/dependencies, inspect storage and understand commands | [Operations reference](docs/OPERATIONS.md) |
| Give another agent the workflow | [Chart-infra skill](skills/chart-infra/SKILL.md) |
| Find this PC's endpoints, verification evidence, investigations and remaining work | [Chart Infra knowledge base](/home/sean/obsidian/vault/Chart%20Infra/INDEX.md) |

The repository is the source of truth for reusable operating instructions and code. The Obsidian knowledge base holds installation-specific facts, reviews, rollout evidence and plans.

## Lifecycle

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

## Implementation boundaries

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
python3 tests/incus_image.py
```

See [verification boundaries](docs/OPERATIONS.md#verification) before running tests that contact the cluster or create fixtures. Read [AGENTS.md](AGENTS.md) before modifying this project.
