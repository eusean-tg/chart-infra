# Chart pilot deployment record — 2026-10-01

Current deployed transport is TLS/SNI on one Tailscale port. Sean has asked whether to disable TLS; the choice between this setup and three plain Mongo ports is pending. Do not interpret the prepared TLS Compass walkthrough as a settled preference until that answer arrives.

The canonical project is `/home/sean/workspace/chart-infra`; private state is `~/.local/state/chart-infra`. Original Go-pipeline tooling remains in its meta workspace. Old DNS/Mongo script paths forward to this project; copied evidence in the old shared-dev state remains historical.

## Installed resources

- Namespace `shared-dev-dns-pilot`: CoreDNS ConfigMap/Deployment, host-network bind only `100.66.127.115:53` UDP/TCP; source ACL allows Tailscale addresses. Serves only `pilot.dev.test`; no global DNS/tailnet changes.
- Namespace `shared-dev-mongo-pilot`: three-member StatefulSet `mongo`, one retained local PV/PVC, a headless Service plus three individual ClusterIP Services, membership/TLS Secret, startup ConfigMap, ingress/egress NetworkPolicy, and HAProxy ConfigMap/Deployment.
- HAProxy binds only `100.66.127.115:27017`; the three TLS SNI names route deterministically to individual member Services. Host-source LAN checks were denied. No new NodePort, LoadBalancer or existing Traefik change.
- Mongo uses authenticated membership and a locally generated admin identity. Compass receives only a read/write user for the synthetic `pilot` database. TLS verification remains enabled in the deployed version.
- Mongo internal replica-set addresses are Kubernetes member FQDNs. The `tailscale` horizon advertises `member-{0,1,2}.mongo.pilot.dev.test:27017` based on TLS SNI. Both internal and external discovery were tested.
- The dedicated namespaces allow host networking for their edge containers. Containers themselves are non-root, drop capabilities (DNS adds only NET_BIND_SERVICE), use read-only roots, and have no Kubernetes API tokens or direct hostPath mounts. Mongo alone mounts the explicitly bound local PVC. Host-network proxy/DNS isolation relies on explicit bind addresses and source ACLs, not ordinary Pod NetworkPolicy.

## Exact versions

- recorded: `2026-10-01`
- node: `sean-trontal`
- k3s: `v1.36.3+k3s1`
- mongo: `7.0.43`
- mongosh: `2.10.0`
- mongo_image: `docker.io/library/mongo@sha256:609d76574151bee8b148caee83cd9fec8b5f695e5a6db9f1e0f9dced0673574e`
- haproxy: `3.2.23-1feef49`
- haproxy_image: `docker.io/library/haproxy@sha256:6343ce34a132a5dceaa24767d739df2bd519f8f7c1079ae39e4821334e8eb42e`
- coredns: `1.14.6`
- coredns_image: `docker.io/rancher/mirrored-coredns-coredns@sha256:900f9c109f7a33545d3c811516e8376df9019147b750f5ce3e254468769176ea`

Mongo 7.0.43 was the available official `mongo:7.0` linux/amd64 image resolved during preparation; deployment uses its immutable digest. Mongo’s release notes list newer 7.0.45, so 7.0.43 is the tested image selection, not a claim of the newest upstream release. See https://www.mongodb.com/docs/manual/release-notes/7.0/.

## Persistence and capacity

HDD: `/mnt/hdd`, ext4 `/dev/sda1`, UUID `14ef1cfa-28ee-4986-8989-2e248896bf07`.

Mongo PV/PVC: PV `shared-dev-mongo-pilot`, PVC `shared-dev-mongo-pilot/mongo-data`, Retain, ReadWriteOnce, advertised 50Gi, static `kubernetes.io/no-provisioner` StorageClass with WaitForFirstConsumer and node affinity. One claim mounts distinct `default/member-0`, `default/member-1`, `default/member-2` subdirectories. About 302 MiB per member after fixtures; this is an observation, not a disk cap. Private credentials, certificates and volume/member inventory live beside the volume under the protected HDD `identity/` directory.

No CPU/memory requests, limits or profile quotas were installed. Each member uses an explicit 0.5 GiB WiredTiger cache setting; this is not a Kubernetes reservation or a limit on total process memory. A post-check sample was approximately 100/96/99 MiB for Mongo members and 9 MiB for HAProxy, with idle-to-test CPU variation; do not use a single sample as a capacity estimate.

## Verification

- Correct TLS certificate and deterministic routing for each of three names on one external port; unknown SNI and missing CA trust rejected.
- Driver discovery returns three external members over the Tailscale endpoint; in-cluster driver returns internal members and reads the same fixture.
- Majority writes and a majority transaction committed; direct secondary writes failed. Unauthenticated reads and admin actions by the pilot user were denied.
- Election recovery: primary changed from member-0 to member-1; a majority write recovered on the same application connection in 131 ms in this one observed run. No guaranteed recovery bound is implied.
- Full runtime removal/recreation preserved PV/PVC UIDs, all member markers and the original persisted fixture.
- Wrong/missing data markers rejected before mongod starts in isolated fake-mount tests. Wrong/missing HDD identity rejected by helper tests. No live HDD unmount or data corruption was used.
- A disposable namespace with unrestricted egress could not connect to any of the three Mongo ClusterIP Services; the namespace was removed afterward.
- Existing workload specifications and cluster DNS matched the pre-pilot snapshot. The independently managed DNS pilot still passes its 16 server checks.
- Mac scoped DNS, Node OS hostname lookup and Mac public-DNS/HTTPS outage continuity previously passed via user-supplied evidence. Default Node SRV failed and SRV is deferred.

Actual Mac Compass, its election recovery, and denial from an actual remote LAN client remain pending. The PC-side protocol client used explicit host mappings, so it is not proof of Compass or Mac resolver behaviour.

## Saved manifests and evidence

All paths below are relative to `~/.local/state/chart-infra/`:

- `dns-pilot/manifest.json`: applied DNS resources.
- `mongo-pilot/manifest.review.json`: rendered Mongo resources with Secret contents redacted; do not apply this review artifact directly.
- `mongo-pilot/proxy-manifest.json`: applied edge proxy resources.
- `mongo-pilot/versions.json`, `verification.json`, `failover.json`, `retention.json`, `storage-guards.json`, `isolation.json`: exact image pins and results.
- `mongo-pilot/compass-uri.txt`: private generated connection URI; `ca.crt`: public CA for client trust.
- `dns-pilot/macos-*-user-reported.json`: Mac evidence with provenance retained.

Reproduce resources with the generators and retained HDD identity; never replace the redacted Secret with production credentials. No source fetch, vendor collector or production action was performed. No existing profile data was moved.
