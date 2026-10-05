---
name: chart-infra
description: Operate the chart-infra host foundation, generic Incus image, personal-box lifecycle and HDD backups. Use chart-box for developer application work and laptop-to-box source synchronization. Exclude unrelated host workloads and production.
---

# Chart infrastructure operator

Establish the host, checkout, box and requested operation. Read repository
AGENTS.md, then [operator procedures](../../incus/README.md) for the task.
Use [architecture](../../incus/ARCHITECTURE.md) for storage/access boundaries.

- `prep.py`: host preparation, tuning and status.
- `image.py`: generic image build, acceptance and provisioning resume.
- `boxes.py`: creation/recreation with retained HDD identity.
- `backup.py`: enrollment, backup, restore checks and timer installation.

Keep host Incus/Docker administration with the operator. Developers are root only
inside their boxes; route application setup and laptop sync to the chart-box skill
and [developer guide](../../incus/DEVELOPER.md). Do not introduce an application
stack into the generic image or operate unrelated host workloads.

Before recreation, coordinate paused source sessions and follow the verified
backup/retention procedure. Preserve the complete authorized-key set and enrolled
identity. Never start two identity copies or bypass a missing data attachment.
Inspect the saved phase before recovering a partial operation. Cleanup requires
explicit targets; stopping retains data.

Use the environment inventory for deployed names, keys, paths and receipts.
Record actual deployment, verification limits, retained artifacts and open work
there; keep reusable procedures in this repository. Do not append session history.
