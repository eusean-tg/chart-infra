---
name: chart-infra
description: Operate chart-infra k3s profiles or prepare its parallel Incus developer-box pilot, including retained data, laptop source sync and agent-led onboarding. Use for chart-infra tasks, not the separate Go pipeline or production services.
---

# Chart infrastructure operator

Establish the target backend (k3s profile or Incus pilot), operator account, checkout path and intended operation before changing a deployment. Use `chart` for k3s. For Incus, read `incus/README.md` and use `incus/prep.py`; its preparation commands do not deploy applications or control laptop sync. A developer's laptop agent owns the laptop checkout and Mutagen sessions; the PC owns runtime configuration, dependencies and retained data.

Incus uses SSD source/dependencies and a required per-box HDD data attachment. Host preparation needs the host operator's sudo. Developers use key-only SSH as root inside their own unprivileged box; they receive no host Incus/Docker/Kubernetes authority. Keep existing k3s profiles and their sessions intact. The remaining workflow below describes k3s; do not pass a box name to its profile activation helpers.

## Establish context

For prepared Incus boxes, follow `incus/BACKING.md`. Copy the reviewed
`incus/backing.py` and `incus/backing_checks.py` together into the box; execute
with its own root SSH identity and explicit `--box`. Image fetch, empty-dataset
preparation and startup are separate commands. Start/stop/down retain HDD data
and credentials; stop/down save the Dragonfly snapshot. Use `--expect-existing`
after restarts to prove retention without recreating persistence tokens. Only one
selected dataset is implemented; box-aware source sync and named
dataset switching remain separate work. Never invoke these helpers on the PC as
a substitute for entering the box.

For Incus backends, follow `incus/APPS.md` and `incus/box.py`. Prepare stock source
bundles, frozen dependency generations and independent private app identities
separately, then select while apps are stopped. Keep the box runtime on its
internal-only network; installer registry access is separate. Stop app writers
before backing lifecycle operations. `app_checks.py` creates retained synthetic
roles/user/workspace and tests API login; its result is not Mac browser evidence.
Box bundle workspaces do not implement Mutagen activation: never aim the k3s
controller or laptop sessions at a selected bundle. Environment imports follow
the agent-reviewed procedure in `incus/APPS.md`, with private retained backups.

Read the checkout's `AGENTS.md` and `README.md`. Discover its location rather than assuming every developer uses `~/workspace`. With this skill inside the checkout, the repository root is two directories above the skill directory; an installed copy requires an explicit checkout path.

Run the bundled helper on the PC:

```sh
python3 <skill-directory>/scripts/context.py --repo <chart-infra-checkout> --profile <developer> --cluster --fingerprints
```

The helper reads allowlisted inventory fields, verifies local storage markers, compares optional source fingerprints, and optionally queries Kubernetes identity, retained volume UIDs and Pod readiness. It prints JSON without reading config/key/credential files or starting a daemon. Missing registration, permissions or prerequisites are reported without repair. Without `--cluster`, cluster identity and volume UIDs are **unverified**. Fingerprints are observations of a mutable tree, not a replacement for `chart_sync freeze`.

Treat the report as diagnostic evidence, not authorization or a substitute for the CLI's operation-specific guards. A stopped profile can be healthy retained state. Do not use a broad test suite to obtain status: integration tests can write fixtures, restart services or create resources.

## Choose the procedure

Read only the relevant canonical guide under the checkout:

| Task | Guide |
| --- | --- |
| Start, stop, connect, recover after laptop reboot | `docs/GETTING-STARTED.md` |
| Laptop pairing, source activation, hot reload, dependency changes | `docs/AGENT-ONBOARDING.md` |
| Import a developer's private `.env.local` | Agent-executed procedure in `docs/AGENT-ONBOARDING.md` |
| Prepare pinned source, Linux runtime and dependency generations; inspect storage and command behavior | `docs/OPERATIONS.md` |

Use `./chart <command> --help` for exact supported arguments. Always pass `--profile`: Mongo defaults to `mongo-pilot-single`, while app and sync commands default to `sean`. `--workspace` names a source workspace; it does not select a Mongo dataset.

Daily startup reuses prepared resources:

```sh
./chart mongo up --profile <developer>
./chart apps up --profile <developer>
./chart apps status --profile <developer>
```

For a source/dependency switch, have the laptop agent freeze its owned sessions, stop the selected profile's apps, install the required frozen Linux dependency generations, explicitly select the prepared workspace, start apps, then have the laptop agent resume and verify transfer. Follow the onboarding guide's precise order. `resume` invalidates the frozen activation attestation. Do not edit the laptop-owned PC mirror, fabricate a checkpoint, reset a dirty checkout, or install dependencies inside a running source mount.

`apps down` and `mongo down` preserve data. Their `--data-only` option removes runtime resources while retaining volumes, data and identities; it does not create an empty dataset. Only the `default` Mongo dataset is implemented. Developer Linux-account/RBAC provisioning and named-dataset switching require implementation; profile arguments do not prove either capability. A second complete app profile still needs isolation and acceptance verification.

## Preserve the environment

- Use only the registered development cluster and verified HDD. Investigate a failed marker, disk identity or PV/PVC guard; do not recreate storage to bypass it.
- Keep source fetching, synchronization, dependency installation and deployment explicit. Startup does not fetch source or run the migration fleet.
- Keep endpoints Tailscale-only and external integrations disabled. Do not use production credentials or live vendor APIs. Any future capture requires a cluster-enforced deadline.
- Keep backend source, Linux dependencies, database files and identities on the profile's retained HDD paths. Keep CPU/memory requests and limits absent; retain explicit database cache tuning.
- Preserve identities, config generations and retained data through teardown. A data deletion requires a specific user instruction naming the target.
- Import private development dotenv settings by agent review and selective merge into protected config. Do not create an import script, sync dotenv files, copy credentials into chat, or replace generated identities wholesale.
- Preserve unrelated services and existing sync configurations. Do not register an automatic Mutagen daemon or change another session's ownership implicitly.

## Record the result

Report the selected profile/workspace, operations performed, checks actually run and any unverified client behavior. Keep reference instructions in the checkout. Put deployment evidence, investigations, reviews, handoffs and remaining work in the chart-infra area of the operator's Obsidian vault; its entrypoint on Sean's PC is `/home/sean/obsidian/vault/Chart Infra/INDEX.md`. Follow that area's index and metadata conventions. Do not add session narratives or copied private runtime inventories to the repository.
