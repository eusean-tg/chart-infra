# Chart development: Sean's pilot

The shared PC now runs Sean's auth → user service → Orange backend stack. Your frontend runs on your laptop. This pilot supports offline sign-in and saved workspaces/layouts. Live market data, external sign-in providers, email delivery, billing and notifications are not connected.

## 1. Connect and start the backend

Connect Tailscale, then run:

```sh
ssh sean@100.66.127.115
cd ~/workspace/chart-infra
./chart mongo up --profile sean
./chart apps up --profile sean
./chart apps status --profile sean
```

Sean's API is `http://100.66.127.115:13000`. Check it from your laptop:

```sh
curl --fail http://100.66.127.115:13000/api/v1/health/services
```

Both `auth` and `user` should report `ok`, with their databases `up`. The endpoint accepts Tailscale connections only. No DNS entry, CA certificate or resolver file is needed for this pilot. Sean confirmed on 2026-10-01 that the laptop frontend works and saves workspaces to this shared backend. The later acceptance report records successful sign-in and workspace save/reload from the Mac, reported by Sean; the browser flow was not independently observed by an agent.

## 2. Point your laptop frontend at it

In your frontend checkout's ignored `.env.local`, set:

```dotenv
VITE_BACKEND_API=/api/v1
VITE_BACKEND_DOMAIN=http://100.66.127.115:13000
VITE_BACKEND_PROXY_TARGET=http://100.66.127.115:13000
VITE_BACKEND_PROXY_ORIGIN=http://localhost:8080
VITE_PORT=8080
```

Use the frontend revision recorded in `sources.lock.json` (the tested revision starts `9939fee27831`), with its normal local dependency setup. Copy the offline Vite wrapper into your checkout once, then start with it:

```sh
scp sean@100.66.127.115:~/workspace/chart-infra/runtime/vite.offline.config.ts \
  qa/verification/shared-dev-offline.config.ts
pnpm run dev --config qa/verification/shared-dev-offline.config.ts --host 127.0.0.1 --strictPort
```

Open `http://localhost:8080/chart/`. The browser uses localhost; Vite proxies API requests to the shared PC. Do not use the remote HTTP IP as your browser's frontend origin: this stack uses Secure session cookies.

This is the application backend connection only. This pilot has no market-data gateway/live feeds. The offline wrapper blocks external browser connections and removes the frontend's market-data proxies. Use it for this pilot; the PC's network restrictions alone do not govern laptop traffic. Blank charts and unavailable external-feature messages are expected.

## 3. Sign in without sending email

Use **Guest Mode → sign in** with a synthetic email ending in `.test`, for example `sean.shared-dev@example.test`. Request the email code, then retrieve it on the PC:

```sh
cd ~/workspace/chart-infra
./chart apps login-code --profile sean --email sean.shared-dev@example.test
```

Enter that code in the browser. A new address will be prompted to choose a username. The code comes from local auth logs; no email is delivered. Application authentication remains enabled.

Sean's synthetic account `chartdevsean` and workspace **Shared dev offline fixture** were retained for verification. You can use a different `.test` address for your own fixture account.

## 4. Work, restart, and stop

```sh
cd ~/workspace/chart-infra
./chart apps logs --profile sean --service orange
./chart apps logs --profile sean --service auth
./chart apps restart --profile sean --service tharamine
./chart apps down --profile sean
./chart apps up --profile sean
```

Services are `auth`, `tharamine`, `orange`, and `redis`. Stopping apps removes API access and preserves Mongo, source, dependencies and keys.

For a complete runtime teardown:

```sh
./chart apps down --profile sean --data-only
./chart mongo down --profile sean --data-only
```

To bring it back, run Mongo `up`, then apps `up` again. The HDD database, identities and volumes remain. Dependency caches and source also remain. These commands never delete datasets or fetch source.

## 5. Edit backend code

Edit the three backend checkouts **on your laptop**, using your existing agent. Mutagen transfers changes to these active read-only runtime mirrors:

```text
/mnt/hdd/shared-dev/profiles/sean/source/mirrors/laptop/auth-service-backend
/mnt/hdd/shared-dev/profiles/sean/source/mirrors/laptop/tharamine-user-service
/mnt/hdd/shared-dev/profiles/sean/source/mirrors/laptop/orange-v2-backend
```

Do not edit those mirrors over SSH. The old `source/workspaces/pilot` checkouts are retained for rollback and are no longer selected. No local backend source patch is required.

The sessions are resumed and watching after laptop acceptance. Use `chart_sync wait` to verify synchronization; after an intentional pause, run `chart_sync resume` first. Give your agent [the onboarding runbook](AGENT-ONBOARDING.md) if that command is not configured in your shell; it uses your recorded paths/session IDs. Each backend runs a source watcher, so TypeScript edits should restart the process without an image rebuild or Pod replacement. The laptop agent verified hot reload in all three backends, including restoration, without replacing their Pods. Requests can briefly fail during a restart.

After a laptop reboot or login, reconnect Tailscale, then run:

```sh
chart_sync status
chart_sync wait
```

Sean's daemon is not registered for automatic login startup. The session query starts it; offline edits then reconcile. Paused/frozen sessions remain paused—run `chart_sync resume` only when ready to resume, then `chart_sync wait`. An intentional deployment freeze should stay paused. [Mutagen documents on-demand daemon startup](https://mutagen.io/documentation/introduction/daemon/).

Optional automatic login startup and its versioned-path upgrade requirements are covered in [the onboarding runbook](AGENT-ONBOARDING.md). Sean has left it disabled; coordinate any change with other workflows sharing the daemon.

Environment changes need an explicit restart. For dependency changes, stop apps, synchronize and freeze, explicitly install Linux dependencies, select the prepared workspace and start apps again. The runner rejects mismatched dependency inputs. Source, dependency caches, config and old volumes remain retained. Existing OpenScape Syncthing is unchanged.

## 6. Inspect Mongo in Compass

Sean's Mongo listens on `100.66.127.115:27018`. Use a service-specific connection file, for example the user-service database:

```sh
ssh sean@100.66.127.115 \
  'cat ~/.local/state/chart-infra/profiles/sean/compass-tharamine-uri.txt' | pbcopy
```

Paste into a new Compass connection with TLS off. The URI sets `directConnection=true`. Alternatives are `compass-auth-uri.txt` and `compass-orange-uri.txt`; each user is restricted to its service's database. These files contain passwords: do not put them in chat, source sync or shared notes.

Mongo data lives under `/mnt/hdd/shared-dev/profiles/sean/mongo/default/member-0`. Existing TimescaleDB/Dragonfly ports and the earlier Mongo pilot are unchanged.

## Sharing and current limits

Teammates may access this API over Tailscale using their own application account. They do not need Sean's SSH credentials to make API calls. For this pilot, Sean retrieves their local sign-in code; per-developer SSH/helper onboarding is later work.

Workspace links are readable by application design. A published layout with `private` access is hidden from other users; another user cannot edit your workspace. This was checked with two synthetic users.

Only Sean's application profile has been deployed. The app Dragonfly instance is a disposable cache; Mongo is the persistent store. Browser cookies are scoped to localhost: use a separate browser profile when switching between teammates’ API targets to avoid mixing sessions. Sean now runs the unmodified laptop mirrors. Real backend hot reload passed in the laptop agent’s checks; the later handoff records successful real Tailscale recovery and Sean-reported frontend sign-in/save/reload; onboarding additional developers, named-dataset switching and live chart data remain later work. Do not use this pilot's synthetic users or keys outside shared development.

## Your Mac data import

On 2026-10-01, your local Mongo archive was imported into Sean's Mongo at `100.66.127.115:27018`: 751 documents, with indexes verified. The Mac's `orangeDB` maps to `orange` here. The existing pilot records were retained, and the apps are healthy after restart.

The original archive and pre-import backup are kept on the HDD at `/mnt/hdd/shared-dev/imports/sean/mongo-rs0-20261001T084046Z/`. The backup filename is `before-import.archive.gz`. Imported roles replace the synthetic bootstrap roles; the old role collection is retained as `auth.roles_pre_import_20261001_084046`.

The PC still uses its own development signing keys and Mongo passwords. Sign in again if a Mac session does not work. The current `login-code` helper still accepts only `.test` addresses; imported accounts with ordinary email addresses are present, but that helper has not yet been extended for them. Email delivery remains disabled.

Backend source and Linux dependency caches now run from the HDD. The old SSD copies and volumes remain as rollback material; no source/data was deleted to reclaim space. The retired source Syncthing identity/config state remains on SSD for rollback; Mutagen stages transferred source beside the HDD mirrors.

## Bringing your own backend .env.local

Your agent can import your development settings. Give it the local file path and service name, and ask it to follow the optional env-import procedure in [the onboarding runbook](AGENT-ONBOARDING.md). No import script is needed. It copies the file privately to your HDD profile, reviews/merges the relevant settings, keeps rollback copies and verifies startup. Your frontend env stays on your laptop.

Laptop database addresses and existing shared-profile keys need different handling, so the agent should not replace the runtime environment wholesale. Production credentials and live integrations remain outside this offline setup. Env/key files remain excluded from Mutagen and Git; ordinary source edits still sync normally.
