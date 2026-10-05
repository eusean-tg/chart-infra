# Chart-infra agent instructions

Read README.md and incus/README.md. Use the chart-box skill for developer work.
Establish the target before acting.

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
- Follow registered root-pool placement for source/dependencies; keep retained data
  on the required HDD /srv/chart/data attachment. Storage moves follow incus/STORAGE.md.
  Monitor capacity; configure no CPU/memory caps or reservations. Preserve
  host-matched timezone, inotify tuning and the Tailscale LAN UDP allowance.
- Before recreation, coordinate paused laptop sessions, stop the box, verify an
  independent HDD backup/scratch restore and retain the SSD rootfs export and old
  stopped instance. Do not start two copies of one Tailscale identity.
- Nightly HDD backup policy: 04:00 Asia/Kuala_Lumpur, seven days of completed nightly
  copies to `/var/backups/chart-incus` on NVMe, stopped copy and restoration of prior
  box running/stopped state. Require explicit host enrollment; exclude managed-pilot,
  image-test, builder and retained-rootfs instances from automatic stops. Manual
  backups, incomplete generations, rootfs exports and source/data are not pruned.
  Do not delete other retained material without explicit scoped authorization.

## Scope

Retained k3s profiles and historical pilot data are outside these tools. Preserve
those resources and unrelated host services. The Go pipeline, Minecraft,
OpenScape and production are outside this project's scope.

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
