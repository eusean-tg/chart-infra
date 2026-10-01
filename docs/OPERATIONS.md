# Operations reference

Chart-infra manages prepared profiles on one registered k3s development PC. It generates Kubernetes objects from Python standard-library modules and keeps runtime state outside the checkout. Use [Agent onboarding](AGENT-ONBOARDING.md) for laptop source synchronization and private environment imports; use [Getting started](GETTING-STARTED.md) for daily operation.

## Host and operator prerequisites

Required PC tools are Python 3, kubectl, Docker, Tailscale, findmnt, Git and OpenSSL. Runtime image builds require the loopback registry at `localhost:5000`. Private source preparation requires GitLab SSH read access; frozen application dependency installs require npm read access in the invoking account's mode-0600 `~/.npmrc`.

There is no host-registration or developer-account/RBAC bootstrap command. An operator must inspect and provision the host, account, filesystem ownership and Kubernetes permissions before running profile preparation. Do not copy another developer's kubeconfig, credentials or identities. Docker access and cluster-level operations are privileged capabilities; separate Linux accounts alone do not establish scoped cluster administration.

`common.guard()` compares the registered node name and `kube-system` namespace UID in private `host.json` with the selected cluster. Mutation paths also require a 25% free reserve on the root filesystem. `mongo.hdd_guard()` checks `/mnt/hdd` against the HDD UUID pinned in `mongo.py`; profile checks verify directory markers and retained volume UIDs. Moving to another host/disk requires an explicit migration, not editing identity values to bypass checks.

`KUBECONFIG` defaults to the invoking user's `~/.kube/config`; an explicit environment override is preserved. This applies to subprocesses launched through non-interactive SSH. Leave the k3s server kubeconfig root-only.

Run mutations as the profile's intended owner and pass `--profile` explicitly. Source-sync preparation requires the profile name to equal the Linux username. Application Pods use the invoking user's UID/GID. Shared account provisioning and scoped cluster permissions need an operator design before another developer can self-administer a profile.

## Components and module map

| File or directory | Responsibility |
| --- | --- |
| `chart` | Dispatches CLI commands to sibling Python modules |
| `common.py` | Private state, registered-cluster guard, lifecycle locks, manifest application |
| `mongo.py`, `checks.py` | Single-member replica set, persistent HDD identity and Mongo verification |
| `apps.py` | Auth/Tharamine/Orange/cache configuration, manifests and workspace selection |
| `access.py` | Chart-only Tailscale TCP proxy and retained route configuration |
| `sources.py`, `sources.lock.json` | Explicit pinned repository preparation |
| `runtime.py`, `runtime/Dockerfile` | Development Node toolchain build and image identity |
| `deps.py` | Frozen Linux dependency generations |
| `sync.py` | PC mirrors, ownership and checkpoint validation |
| `laptop-sync.py`, `sync_common.py` | Laptop-owned Mutagen sessions and shared source policy |
| `runtime/launch.cjs` | Protected configuration loading and `tsx watch` startup |
| `runtime/vite.offline.config.ts` | Laptop frontend API proxy and browser connection restrictions |
| `tests/` | Offline unit checks and explicitly scoped integration fixtures |
| `skills/chart-infra/` | Agent workflow and context helper |

Backend mapping:

| Service | Source repository | Internal port | Mongo database |
| --- | --- | --- | --- |
| `auth` | `auth-service-backend` | 4001 | `auth` |
| `tharamine` | `tharamine-user-service` | 5001 | `orange` |
| `orange` | `orange-v2-backend` | 3000 | `orangeV2` |
| `redis` | Pinned Dragonfly image | 6379 | None; disposable cache |

Each backend receives a separate Mongo user with read/write access to its database. Runtime config mounts are separate from source. App containers use a read-only root filesystem, read-only source/dependency/config mounts, writable temporary storage, dropped capabilities and no service-account token.

## Prepare a profile

Use explicit names and unused reserved ports. The following is an operator workflow on an already registered host with account/access provisioning complete, not an unattended onboarding script.

1. Prepare and start Mongo first. Source and dependency preparation require its retained inventory; mirror preparation also creates bound source volumes.

   ```sh
   CHART_PROFILE=your-profile
   CHART_WORKSPACE=baseline
   ./chart mongo prepare --profile "$CHART_PROFILE" --port MONGO_PORT
   ./chart mongo up --profile "$CHART_PROFILE"
   ```

2. Choose source ownership. For laptop-owned checkouts, follow [Agent onboarding](AGENT-ONBOARDING.md) and freeze them before installing dependencies. For pinned PC checkouts:

   ```sh
   ./chart sources prepare --profile "$CHART_PROFILE" --workspace "$CHART_WORKSPACE"
   ./chart sources status --profile "$CHART_PROFILE" --workspace "$CHART_WORKSPACE"
   ```

   This prepares all four repositories in `sources.lock.json`: three backends and the frontend, at exact commits. Existing paths must have the expected origin/revision. Dirty edits are preserved; a different revision requires an explicit new workspace. Preparation never resets existing checkouts or applies backend patches. It does not initialize a broader meta workspace.

3. Reuse an inspected runtime generation or explicitly build one:

   ```sh
   ./chart runtime build
   ```

   The build publishes to the loopback registry and records exact image and package inventory in private `node-runtime.json`. A rebuild can select different Debian package revisions; treat its result as another runtime generation and prepare compatible dependencies explicitly.

4. Install frozen Linux backend dependencies:

   ```sh
   for service in auth-service-backend tharamine-user-service orange-v2-backend; do
     ./chart deps install --profile "$CHART_PROFILE" --workspace "$CHART_WORKSPACE" --service "$service"
   done
   ```

   The installer mounts `~/.npmrc` read-only, outside image layers and runtime Pods. Multiple registry host/path entries are allowed; duplicate `_authToken` entries for the same key do not provide fallback. Repository-local `.npmrc` requires review before installation. Dependencies remain on HDD, keyed by image identity and dependency-input hash. A partial/unregistered cache is retained and rejected for inspection.

5. Prepare retained app identities and activate prepared source:

   ```sh
   ./chart apps prepare --profile "$CHART_PROFILE" --workspace "$CHART_WORKSPACE" --port API_PORT
   ./chart apps select --profile "$CHART_PROFILE" --workspace "$CHART_WORKSPACE"
   ./chart apps up --profile "$CHART_PROFILE"
   ```

   Use the actual reserved numeric ports in place of `MONGO_PORT` and `API_PORT`. Existing identities and ports cannot be implicitly reassigned. Preparing apps creates signing identities, service auth configuration and database credentials; startup creates missing profile-scoped Mongo users. Compatible application roles/data require separate preparation and verification. There is no generic migration, role-seeding or Mongo archive-import command in chart-infra.

Source fetching, runtime builds and package installation need their own network access. They are distinct from application runtime traffic, which remains constrained by app network policies and development integration settings.

## Command reference

Run `./chart COMMAND --help` for syntax. Defaults differ: Mongo defaults to the standalone pilot profile; most app/source commands default to `sean`. Supply profile and workspace deliberately.

| Command | Behavior |
| --- | --- |
| `mongo prepare --profile NAME [--port PORT]` | Initializes an empty `default/member-0` once and creates retained identity/port reservation |
| `mongo up --profile NAME` | Validates disk/data/volume identity, starts Mongo and its route; never replaces missing initialized data |
| `mongo down --profile NAME [--data-only]` | Withdraws the route and stops runtime; data-only also removes runtime objects while retaining storage/identity |
| `mongo status --profile NAME` | Inspects namespace objects |
| `mongo check --profile NAME` | Runs authenticated fixture/readiness/storage/network checks against a running profile |
| `sources prepare/status --profile NAME --workspace NAME` | Fetches pinned checkouts explicitly, or reports their recorded inventory |
| `runtime build` | Builds/publishes the pinned development toolchain and records its result |
| `deps install --profile NAME --workspace NAME --service REPO` | Prepares a frozen Linux dependency generation; does not activate it |
| `apps prepare --profile NAME --workspace NAME [--port PORT]` | Creates retained development configuration and identities once |
| `apps select --profile NAME --workspace NAME` | Selects prepared source/dependency/config volumes while apps are stopped |
| `apps up/down --profile NAME [--data-only]` | Starts prepared apps or stops runtime; `--data-only` applies only to down |
| `apps status/logs/restart --profile NAME [--service SERVICE]` | Inspects runtime, reads recent logs, or restarts one Deployment |
| `apps prepare-backend-key --profile NAME` | Provisions a missing retained auth backend signing key, preserving existing identity |
| `apps login-code --profile NAME --email EMAIL` | Reads the recent local sign-in code for a synthetic `.test` address |
| `sync prepare/status/export --profile NAME [--workspace NAME]` | Prepares mirrors or reads their registration/status; does not operate the laptop daemon |
| `laptop-sync.py plan/setup/resume/pause/status/wait/freeze ...` | Manages only the registered owned laptop sessions |

`sync register/checkpoint/invalidate` are protocol endpoints used by the laptop helper. Do not handcraft these payloads to bypass validation. `sync upgrade-policy` supports only its known unused-registration upgrade; it refuses populated or registered sessions needing a coordinated migration.

`sources relocate` and `deps relocate` preserve originals while moving supported existing generations to HDD. They are operator migration operations, not startup steps. Inspect existing paths and retained receipts before using them.

## Lifecycle, storage and recovery

| Operation | Stops/removes | Retains |
| --- | --- | --- |
| `apps down` | API route; app/cache replicas | Mongo, app objects, source, dependencies, identities and volumes |
| `apps down --data-only` | API route; app/cache Deployments, Services and policies | Namespace, data, identities and all source/dependency/config volumes |
| `mongo down` | Mongo route; Mongo replicas | Runtime objects, data, identity and volumes |
| `mongo down --data-only` | Mongo route/controller/Services/config/Secret/policy | Namespace, storage class, data, identity and PV/PVC |
| Workspace selection | Changes selected prepared mounts; apps must be stopped | Previous workspace/dependency/config generations and identities |

Stop apps before Mongo. Start Mongo before apps. Pausing source transfer is a separate laptop action. There is no delete-data command, automatic cache garbage collection, named-dataset switch or source reset.

Persistent profile layout:

```text
/mnt/hdd/shared-dev/profiles/<profile>/
  mongo/default/member-0/                 Mongo files
  identity/                              Mongo identity and inventory
    apps/                                Canonical app config, keys and inventories
      runtime-configs/                   Retained generated config generations
    env-imports/                         Private env imports and rollback records
  source/workspaces/<workspace>/<repo>/   PC-owned pinned checkouts
  source/mirrors/<workspace>/<repo>/      Laptop-owned mirrors
  dependencies/<repo>/<input-hash>/       Linux packages and install identity
```

Mongo uses one statically bound local Retain PV/PVC per profile with member data in a subdirectory. Source, dependency and config generations use their own retained references. Kubernetes local-volume paths and binding identities are not rewritten to switch data. Mongo dataset switching is unimplemented; retain the ordinal layout for an explicit future design.

Private state defaults to `~/.local/state/chart-infra/` and can be relocated through `CHART_INFRA_STATE` only as a deliberate operator setup. It includes `host.json`, `node-runtime.json`, source/dependency records, route inventory, generated manifests and profile connection exports. Keep this directory outside Git and source sync. `identity/apps/inventory.json` on HDD records selected workspace/mounts and volume UIDs.

`profiles/<profile>/manifest.review.json` is a redacted Mongo review artifact, not an apply-ready manifest. `profiles/<profile>/apps-manifest.json` records applied app objects. `access-releases/<hash>/haproxy.cfg` and `access-routes.json` record chart routes. Regenerate through the owning runner after validating state; do not blindly apply stale artifacts.

Missing disks, initialized database files, markers, volumes or keys require investigation. Do not recreate identities or delete PVCs to make startup succeed. Selecting a previous prepared workspace is the supported source rollback; database restore and identity replacement require explicit scoped procedures.

## Network, resource and version policy

The `chart-infra-access` Docker proxy binds only the registered Tailscale IPv4 address and rejects sources outside `100.64.0.0/10`. It forwards API/Mongo TCP ports to their cluster Services. Mongo uses one plain IP:port route per profile. In-cluster discovery uses `mongo-0.mongo.chart-<profile>.svc.cluster.local:27017`, replica set `rs0`; laptop clients use `directConnection=true`.

Route-set updates validate configuration before replacing the chart proxy and retain its predecessor until the replacement starts. This can briefly reconnect clients across chart profiles. The proxy is separate from other shared-development proxies.

Application NetworkPolicies allow profile application services, Mongo and cluster DNS. They provide workload network isolation, not protection against a privileged host/cluster administrator. Keep supported development integration flags and inspect changes to worker destinations. No production credentials, external delivery or live capture are configured through the standard preparation flow. Missing credentials alone is not a network control.

Workloads have no CPU/memory requests or limits. Mongo uses a 0.5 GiB WiredTiger cache and 256 MiB oplog configuration; neither caps total resource use. PV declarations are 50Gi for Mongo and 10Gi for app mounts, describing local directories rather than reserved/preallocated space or enforced quotas.

| Component | Version identity |
| --- | --- |
| Node / pnpm | `24.20.0` / `11.28.2`, pinned in runtime/Dockerfile |
| Mongo image | Exact digest in mongo.py |
| Dragonfly image | Exact digest in apps.py |
| Access proxy image | Exact digest in access.py |
| Mutagen | `0.18.1`, pinned in sync_common.py |
| Prepared source | Exact revisions in sources.lock.json; laptop checkpoints record actual HEAD/dirty state |
| Built runtime | Image ID, registry digest and installed package inventory in private node-runtime.json |
| Dependencies | Repository frozen pnpm lockfile plus runtime/dependency-input hash |

Read pins from these owning files when changing versions. Record actual deployments and validation in the installation knowledge base rather than treating a source pin as proof of what is running.

## Frontend compatibility

The offline wrapper integrates with `kiyotaka-frontend` revision `9939fee27831cfd3137dc21fd6ecce2bc42872e6` and its `qa/verification/vite.config.ts`. Place the wrapper beside that file. Other revisions require inspection of the imported config and Vite proxy behavior before use.

The wrapper retains application API proxies, blocks known market-data proxy paths, and sets a restrictive browser content-security policy. PC network policy does not govern browser traffic, so retain this separate laptop control. Frontend env/proxy commands are in [Getting started](GETTING-STARTED.md).

## Verification

Offline unit checks use temporary state or mocks without deploying workloads:

```sh
python3 tests/sync_safety.py
python3 tests/kubeconfig.py
python3 tests/stock_source.py
python3 tests/backend_key.py
python3 tests/mongo_checks.py
python3 tests/skill_context.py
```

Integration checks have explicit operating requirements:

| Check | Scope and effects |
| --- | --- |
| `./chart mongo check --profile NAME` | Running profile; synthetic `chart` database fixture, discovery/auth/transaction/change-stream and deployment checks |
| `python3 tests/storage_guards.py --profile NAME` | Registered profile/cluster and Docker; exercises fake missing/mismatched files, never unmounts or corrupts live data |
| `python3 tests/profile_isolation.py --profile-a A --profile-b B` | Two prepared running Mongo profiles; credential, data and network isolation using synthetic records |
| `python3 tests/app_egress.py --profile NAME` | Running apps; creates a deadline-bound controlled endpoint in a temporary namespace, tests Tharamine egress denial and allowed Mongo access, then removes fixture objects |
| `python3 tests/mutagen_runtime.py` | Operator integration fixture using local SSH, Mutagen, Docker/k3s and retained HDD fixture volumes; inspect its configured host/profile prerequisites first |
| `python3 tests/noninteractive_ssh.py` | Extends the operator fixture with non-interactive SSH command/lock checks; not a substitute for an actual developer laptop |

The egress check contacts no public API. It proves the tested cross-namespace path is denied while its controlled target is reachable from the host; it does not prove protection against privileged host access, every external destination or future policy changes. A single Mongo member cannot prove secondary routing or failover behavior.

Retain private evidence paths and dates in the knowledge base. Full acceptance includes actual laptop transfer, hot reload in all three backends, Tailscale recovery, browser sign-in/save/reload and profile isolation. Do not label PC fixture results as actual Compass or laptop-browser verification.
