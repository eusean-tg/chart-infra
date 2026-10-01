# Operator preparation — Sean's offline pilot

Daily use is in [GETTING-STARTED.md](GETTING-STARTED.md). These are explicit preparation operations, not part of app startup. The existing Sean pilot is already prepared; do not rerun bootstrap just to start it.

## Authentication and source

Use the registered PC account with Docker/kubectl access, GitLab SSH read access and a local `~/.npmrc` with read access to the private `@orangecharts` dependencies. Never put token values into chat or the project. The npm config may contain credentials for multiple registry hosts/paths, but duplicate entries for the same `_authToken` key do not provide fallback: the last value wins. The current machine's effective entry was verified against all five required private packages. Keep the file mode `0600`.

```sh
cd ~/workspace/chart-infra
chmod 600 ~/.npmrc
./chart sources prepare --profile sean --workspace pilot
./chart sources status --profile sean --workspace pilot
```

This clones only the five repositories in `sources.lock.json`, at exact commits, into `/mnt/hdd/shared-dev/profiles/sean/source/workspaces/pilot`. Backend source is left unmodified. Only the synthetic role fixture patch for `script-migration` is applied; existing dirty checkouts are never reset. The previous Tharamine patch is archived under `retired/2026-10-01-offline-worker-patch/` and is no longer applied or required. Existing pilot edits remain retained for rollback. No application submodules are required for this pilot. This is separate from meta_repo's broader `./meta init` operation and its Go HTTPS module-discovery authentication.

## Runtime and dependencies

The build uses the existing loopback registry at `localhost:5000`. Node 24.20.0 and pnpm 11.28.2 are pinned. The base image is pinned by digest; the installed OS package inventory and built image digest are recorded in private state. A later rebuild can pick newer Debian package revisions and must be treated as a new runtime generation.

```sh
./chart runtime build
for service in auth-service-backend tharamine-user-service orange-v2-backend script-migration kiyotaka-frontend; do
  ./chart deps install --profile sean --workspace pilot --service "$service"
done
```

Frozen Linux installs run inside the toolchain image. `~/.npmrc` is a read-only installer mount, not an image layer or runtime mount. Dependency volumes are keyed by runtime identity and dependency-input hashes. Backend source, dependency caches, Mongo and persistent application identities live on HDD. Retired source Syncthing state is retained on SSD for rollback; new transfer uses Mutagen over SSH with staging beside HDD mirrors. Initial SSD source/dependency copies remain as rollback material.

## First preparation and fixture

```sh
./chart mongo prepare --profile sean
./chart mongo up --profile sean
./chart apps prepare --profile sean --workspace pilot
./chart apps up --profile sean
./chart fixtures
./chart fixtures --apply
```

The fixture command is deliberately Sean-pilot-specific. It runs only `20261001_001_shared_dev_roles` from script-migration, first dry-run, then apply. It creates missing synthetic guest/beta roles without replacing existing ones. It does not invoke the migration fleet or seed production data. Jobs have a 180-second cluster deadline. Completed jobs are not replayed; retained migration state and idempotent fixture operations survive runtime teardown.

Normal account creation/login uses the application API and UI. Auth's email transport is pointed to closed loopback with dummy development credentials and bounded retries; the local code helper reads the generated code from the auth Pod log.

## Current lifecycle boundary

`apps up/down` reuse the prepared source paths, dependency volumes, config and image. TypeScript edits hot reload. Source fetch, dependency install and deployment remain separate. **Selection is now explicit:** stop apps, run `./chart apps select --profile sean --workspace NAME`, then start apps. Selection validates prepared dependency inputs and runtime identity and uses separate retained volume references; installing a generation alone never activates it. Synced sources require a matching laptop checkpoint and paused owned folders. See the agent runbook; do not edit PV paths, delete PVCs or overwrite identities to bypass guards.

Named Mongo dataset switching and Linux-account/RBAC onboarding remain planned. Sean’s HDD mirrors are registered for Mutagen over SSH; the Mac has registered three sessions and verified first transfer; activation and real backend watcher acceptance are separate checks. Run laptop freeze before installing dependencies or selecting synced source. The dedicated source Syncthing runtime is retired with all state retained; OpenScape Syncthing is unchanged. The runner supports profile arguments, but only Sean's complete app stack has passed acceptance; bootstrap and full browser/isolation checks for a second app profile are still required.

## Auth backend signing identity

New profiles provision a separate retained development RSA-2048 backend key during `apps prepare`. For a previously prepared profile missing `PRIVATE_KEY_PATH_BACKEND`, explicitly run:

```sh
./chart apps prepare-backend-key --profile sean
./chart apps restart --profile sean --service auth
```

This adds the key in the protected auth config directory on the HDD, already mounted read-only at `/run/chart`, and retains a backup of the prior config. Repeating provisioning preserves the key. Missing configured keys and conflicting paths fail for inspection rather than regeneration. The shared auth configuration covers both `pilot` and `laptop`; existing user/service JWT identities are unchanged. No source fetch, dependency install, database write or laptop key copy is involved.

## Optional developer dotenv settings

Agents may import a developer's private development `.env.local` using section 7 of [AGENT-ONBOARDING.md](AGENT-ONBOARDING.md). This is an agent-executed review/merge/verification procedure, not an import script. Keep canonical per-service config separate from retained generated runtime configurations, preserve profile-owned identities and endpoints, and retain previous values privately for rollback. Common onboarding failures and their remedies are in section 8 of that guide.
