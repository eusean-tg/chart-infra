# Set up and use a chart development profile

The shared PC runs your backend services and Mongo. Your frontend and coding agent run on your laptop. This guide assumes an operator has prepared your Linux account, profile, backend source and dependencies. Give your agent the [onboarding runbook](AGENT-ONBOARDING.md) for source synchronization and configuration setup.

Get these values from your operator or the installation's [environment inventory](/home/sean/obsidian/vault/Chart%20Infra/INDEX.md): your SSH account/host, the chart-infra checkout path, your profile name and your API URL. Examples use placeholders deliberately; teammates' API access does not require sharing SSH credentials.

## 1. Connect and start

Connect Tailscale and SSH to your prepared account. In the chart-infra checkout:

```sh
CHART_PROFILE=your-profile
./chart mongo up --profile "$CHART_PROFILE"
./chart apps up --profile "$CHART_PROFILE"
./chart apps status --profile "$CHART_PROFILE"
```

The startup output gives the API URL. From the laptop, substitute it below:

```sh
CHART_API_URL=http://tailscale-ip:profile-api-port
curl --fail "$CHART_API_URL/api/v1/health/services"
```

Auth and user-service health must report `ok`, with their databases `up`. These endpoints use plain HTTP over Tailscale. No CA certificate, SRV record or laptop resolver file is required.

## 2. Connect the laptop frontend

In the frontend checkout's ignored `.env.local`, substitute the profile's API address:

```dotenv
VITE_BACKEND_API=/api/v1
VITE_BACKEND_DOMAIN=http://tailscale-ip:profile-api-port
VITE_BACKEND_PROXY_TARGET=http://tailscale-ip:profile-api-port
VITE_BACKEND_PROXY_ORIGIN=http://localhost:8080
VITE_PORT=8080
```

Copy `runtime/vite.offline.config.ts` from chart-infra to `qa/verification/shared-dev-offline.config.ts` in the frontend checkout. It imports the adjacent `vite.config.ts`; [Operations](OPERATIONS.md#frontend-compatibility) records the compatible frontend revision. Preserve an existing destination until its contents have been reviewed.

Start the frontend with its prepared local dependencies:

```sh
pnpm run dev --config qa/verification/shared-dev-offline.config.ts --host 127.0.0.1 --strictPort
```

Open `http://localhost:8080/chart/`. Keep localhost as the browser origin: the application uses Secure session cookies, and Vite proxies API traffic to the shared PC. The wrapper restricts browser connections and disables market-data proxies. Blank charts and unavailable external-feature messages are expected; this environment supports offline application work, not live feeds.

## 3. Sign in and save a workspace

Use **Guest Mode → sign in** with a synthetic `.test` email address, such as `developer@example.test`. Request a code, then retrieve it on the PC:

```sh
./chart apps login-code --profile "$CHART_PROFILE" --email developer@example.test
```

Enter the code in the browser. New accounts choose a username. The helper reads the local auth log; email is not delivered. It accepts only `.test` addresses and looks back ten minutes. If the log has no matching code, request a fresh one.

Save a workspace, reload the page and confirm the saved state. Successful service health alone does not verify browser sign-in, cookies or persistence. A fresh profile also needs compatible application roles/data prepared by its operator; chart-infra has no generic role-seeding or migration command.

## 4. Edit backend source

Edit the backend checkouts on your laptop. Mutagen sends changes to HDD mirrors; each backend runs `tsx watch`. Do not edit the PC mirrors directly. TypeScript edits restart the process without replacing its Pod or rebuilding an image; requests can briefly fail during the restart.

Use the `chart_sync` function configured by your agent:

```sh
chart_sync status
chart_sync wait
```

`wait` verifies running sessions and matching content. If sessions are paused, it refuses to proceed. Keep an intentional deployment freeze paused; otherwise run `chart_sync resume`, then `chart_sync wait`.

After laptop reboot or login, connect Tailscale and run `chart_sync status`. The query starts the daemon on demand unless autostart is disabled. Saved paused sessions stay paused. Automatic login startup is optional; ask your agent to follow the daemon instructions in the onboarding guide.

Dependency changes require the explicit freeze/install/select workflow in [Agent onboarding](AGENT-ONBOARDING.md#4-install-dependencies-and-select-source). Backend environment changes require a reviewed private configuration update and restart; they do not transfer through source sync.

## 5. Inspect, restart or stop

On the PC:

```sh
./chart apps logs --profile "$CHART_PROFILE" --service orange
./chart apps restart --profile "$CHART_PROFILE" --service tharamine
./chart apps down --profile "$CHART_PROFILE"
./chart apps up --profile "$CHART_PROFILE"
```

Service names are `auth`, `tharamine`, `orange` and `redis`. Logs can contain application data; inspect them privately before sharing extracts. Stopping apps removes API access and keeps Mongo, source, dependencies and keys. It does not pause Mutagen.

For a runtime teardown, first pause your source sessions on the laptop, then on the PC:

```sh
./chart apps down --profile "$CHART_PROFILE" --data-only
./chart mongo down --profile "$CHART_PROFILE" --data-only
```

Restart Mongo before apps. All data and volumes remain on the HDD. The app cache is disposable; Mongo stores persistent application data. No command here deletes a dataset or fetches source.

## 6. Connect MongoDB Compass

Your operator provides a private service-specific URI from:

```text
~/.local/state/chart-infra/profiles/<profile>/compass-auth-uri.txt
~/.local/state/chart-infra/profiles/<profile>/compass-tharamine-uri.txt
~/.local/state/chart-infra/profiles/<profile>/compass-orange-uri.txt
```

Transfer the chosen URI through your own SSH account or a private clipboard. Paste it into Compass with TLS off. The URI includes `directConnection=true`; each service user can access its own database. These files contain passwords: keep them out of chat, Git, source sync and shared notes.

## Share API access and import settings

Teammates on Tailscale can use your API with their own application accounts. They do not need your SSH credentials. Use separate browser profiles when switching API targets to avoid mixing localhost sessions.

To bring backend development settings, give your agent the `.env.local` path and service name. It follows the [private import procedure](AGENT-ONBOARDING.md#6-optional-development-envlocal-import), reviews and merges supported settings, preserves profile keys/endpoints, retains backups and verifies the feature. There is no import script. Frontend environment files stay on the laptop; backend dotenv and key files remain excluded from synchronization.
