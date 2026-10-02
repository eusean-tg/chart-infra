# Chart-infra agent instructions

Read README.md. For personal Incus boxes, read incus/README.md and use the
chart-box skill for developer work. For retained k3s profiles, read
docs/OPERATIONS.md and docs/AGENT-ONBOARDING.md. Establish the target before acting.

## Personal Incus boxes

Infrastructure supplies the host boundary, a generic Ubuntu/Docker/nvm image,
identity-preserving box lifecycle, laptop Mutagen sync and HDD backups. Developers
are root inside their boxes and own their repositories, dependencies, environments,
keys, services and data. Follow each application's own instructions. Do not turn
application assumptions into host/image gates or introduce an application box CLI.

- Read incus/IMAGES.md for generic image build/acceptance and incus/DEVELOPER.md
  for agent-led setup. No application source, service images, npm credentials,
  source pins or seeded dependency generations belong in the image.
- Use skills/chart-box/scripts/sync.py on the laptop for arbitrary repositories.
  Discover paths and existing ~/.config/chart-box/*.json mappings; pass the selected
  --config on every helper invocation. Flush and verify before remote
  work that depends on edits. Preserve unrelated sessions and configuration.
- Laptop mirrors are source-owned by the laptop. Create private configuration and generated
  dependencies in the box as needed. Tracked files are not secret-scanned;
  .git, dependencies and build/cache outputs stay excluded. The helper's tracked
  exceptions are explicit session policy; pause/refresh when those paths change.
- Box egress is normal Internet access. Shared-infrastructure live-capture restrictions
  do not govern personal boxes. Use developer-owned credentials for authorized
  application tasks; do not acquire or reuse production credentials implicitly.
- Keep host Incus/Docker/Kubernetes administration with the operator. Guest root
  remains unprivileged on the host. Preserve host firewall, HDD identity checks,
  isolated UID maps and required attachments. Tailnet ACLs govern overlay access.
- Use SSD for source/dependencies and HDD /srv/chart/data for retained data.
  Monitor capacity; configure no CPU/memory caps or reservations. Preserve
  host-matched timezone, inotify tuning and the Tailscale LAN UDP allowance.
- Preserve Sean's deployed pilot and laptop sessions until a separately executed
  migration. Legacy Incus app helpers remain only for that deployed stack. Do not
  run legacy app acceptance against a developer-owned environment.
- Before recreation, coordinate paused laptop sessions, stop the box, verify an
  independent HDD backup/scratch restore and retain the SSD rootfs export and old
  stopped instance. Do not start two copies of one Tailscale identity.
- Nightly HDD backup policy: 04:00 Asia/Kuala_Lumpur, seven days of completed nightly
  copies to `/var/backups/chart-incus` on NVMe, stopped copy and restoration of prior
  box running/stopped state. Require explicit host enrollment; exclude managed-pilot,
  image-test, builder and retained-rootfs instances from automatic stops. Manual
  backups, incomplete generations, rootfs exports and source/data are not pruned.
  Do not delete other retained material without explicit scoped authorization.

## Retained shared k3s environment

Use ./chart with an explicit --profile and the profile's registered Linux owner.
Preserve host/HDD markers, PV/PVC identity, credentials and data through teardown.
Keep source fetching, deployment and live capture separate. Vendor collection,
external metadata refresh and history fetching stay disabled by default; future
shared capture requires a cluster-enforced deadline. No production changes.
Keep existing source/config isolation, resource policy and developer access guards.
The Go pipeline, Minecraft, OpenScape and unrelated services are outside this task.

## Documentation and verification

Apply definitive-docs. Keep reusable code and operating guides here. Put inventory,
evidence, investigations and roadmap in /home/sean/obsidian/vault/Chart Infra using
its index conventions. Distinguish implemented tools, automated checks, operator
execution and laptop/user acceptance. Inspect integration tests before execution;
use isolated fixtures and never reset existing datasets to pass a test.
Record persistent out-of-repo staging, caches, exports and one-off helper paths in
the vault's artifact inventory when creating them. Include purpose, sensitivity,
owner and cleanup condition; update the record after cleanup. A cache location is
not deletion authorization. Keep reusable helpers in this repository.
