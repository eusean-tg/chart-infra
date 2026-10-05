# Chart-infra agent instructions

Read README.md, then the guide for the task. Establish the host, box and intended
operation before acting. Use the chart-infra skill for operator work and chart-box
for developer applications and laptop source sync.

- Keep host administration with the operator. Preserve unrelated host workloads.
- Keep the image generic. Follow application instructions; do not add application
  deployment gates, source seeds or an infrastructure application CLI.
- Use developer-owned credentials only within the task; do not acquire or reuse
  production credentials implicitly.
- Edit synced source on the laptop. Select its recorded mapping, preserve unrelated
  sessions and flush before remote work that depends on edits.
- Preserve data and identities. Read the operator recreation procedure before
  replacement; inspect partial failure records instead of retrying blindly.
  Never run two copies of an enrolled identity. Deletion needs explicit scope.
- Inspect live tests before execution. Use isolated fixtures and never reset
  existing datasets to make a test pass.

Apply definitive-docs. Write reusable procedures here and deployment facts/open
work in `/home/sean/obsidian/vault/Chart Infra` using its index conventions.
Document the present system; Git holds completed investigations and edit history.
Record persistent external artifacts and cleanup conditions in the vault inventory.
A listed cache or backup path is not deletion authorization. Distinguish deployed
behavior from implementation and verification limits.
