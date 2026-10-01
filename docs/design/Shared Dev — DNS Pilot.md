---
updated: 2026-10-01
status: retired; Mongo no longer requires private DNS
---

# Retired DNS pilot — historical verification

**Retired by Sean’s final 2026-10-01 decision.** The DNS Deployment/ConfigMap/namespace have been removed. Mongo now uses Tailscale IP:port with directConnection=true and needs no CA or resolver file. Remove the old Mac `/etc/resolver/pilot.dev.test` using [[Shared Dev — Mongo Pilot]]. API hostname DNS is a separate future decision. The sections below record historical tests; their deployment commands are retired and must not be run as current onboarding.


This is a real, isolated CoreDNS pilot in the shared PC’s k3s cluster. It serves **only `pilot.dev.test`** at **`100.66.127.115:53`**, over UDP and TCP. The DNS records began as synthetic probes. The three member names now point to the isolated Mongo pilot described in [[Shared Dev — Mongo Pilot]]; a successful DNS query alone still does not establish Compass connectivity.

Sean requested this check before committing the frontend-backend design to DNS/SRV. No tailnet DNS settings, existing cluster DNS, profile databases, source sync, Minecraft or Traefik configuration have been changed. The isolated Mongo pilot is deployed and PC-verified; full frontend-stack implementation is still pending. Existing unrelated manifests remain in `~/manifests`; shared-dev tooling stays in the meta workspace.

## What passed on the PC

- 16 assertions covering A, TXT and three-member SRV answers over both UDP/TCP; authoritative NXDOMAIN for a missing pilot name; no recursion for unrelated domains; refusal of LAN-sourced queries even when sent to the Tailscale IP; and no DNS listener on the LAN IP.
- An eight-second stop/restart drill: DNS refused connections while down, then recovered and passed all 16 checks again.
- Reapplying the manifest reported unchanged resources.
- All pre-existing Deployment/StatefulSet/DaemonSet/Service specifications and identities, plus the existing cluster DNS ConfigMap, matched the before snapshot.
- An idle sample was 5m CPU and 11 MiB RAM; this is one sample, not a capacity benchmark. The pilot sets no CPU/memory requests or limits.

Sean supplied macOS terminal results on 2026-10-01: direct UDP A returned `100.66.127.115`, and both UDP and TCP SRV queries returned all three expected member names on port 27017. Remote Tailscale DNS reachability is now confirmed by those user-reported results, saved in `~/.local/state/chart-infra/dns-pilot/macos-direct-user-reported.json`. No remote TXT result was supplied yet. Native macOS resolution was subsequently verified as recorded below. Laptop public DNS/HTTPS continuity during the pilot outage passed as recorded below. Denial from an actual LAN client remains pending. Mongo TLS/SNI routing and real Compass failover remain a separate acceptance gate.

**Native Mac-only resolver passed (Sean’s supplied output, 2026-10-01):** `dscacheutil` resolved fresh `check-1790829496.probe.pilot.dev.test` to `100.66.127.115`; `dns-sd` returned all three SRV records on port 27017. Evidence: `~/.local/state/chart-infra/dns-pilot/macos-native-user-reported.json`. This verifies the local `/etc/resolver/pilot.dev.test` path, not a tailnet-admin DNS change. The subsequent default Node SRV check failed as recorded below. Mac internet continuity during the outage subsequently passed. Actual Mongo/Compass integration remains pending.

**Default Node SRV failed (Sean’s supplied output, 2026-10-01):** the `Promise.all` diagnostic using `dns.resolveSrv('_mongodb._tcp.mongo.pilot.dev.test')` and `dns.resolveTxt('mongo.pilot.dev.test')` returned `ENOTFOUND querySrv ENOTFOUND _mongodb._tcp.mongo.pilot.dev.test`. TXT has no reported outcome because the combined promise rejected. Node version was not supplied; actual Compass was not tested. Evidence: `~/.local/state/chart-infra/dns-pilot/macos-node-user-reported.json`.

The Mac-only resolver is therefore insufficient for default Node SRV discovery in the tested configuration. This is consistent with Node record-query APIs bypassing OS hostname-resolution facilities. Do not change global DNS to make this test pass. [Node DNS behaviour](https://nodejs.org/api/dns.html#implementation-considerations)

**Selected first-version approach (Sean, 2026-10-01), Mongo integration still to validate:** use a generated standard `mongodb://` URI listing all three member hostnames, with explicit TLS and replica-set options. Node `dns.lookup` for all three member hostnames passed on Sean’s Mac. Next test the actual Compass connection, member discovery and election recovery once Mongo exists. Standard member-list URIs preserve replica-set discovery; they do not require SRV records. An administrator-managed DNS route remains an alternative to test for `mongodb+srv://`. [Mongo connection string formats](https://www.mongodb.com/docs/manual/reference/connection-string-formats/)

**Node OS member lookup passed (Sean’s supplied output, 2026-10-01):** `dns.lookup` returned `{ address: '100.66.127.115', family: 4 }` for each of `member-0.mongo.pilot.dev.test`, `member-1.mongo.pilot.dev.test` and `member-2.mongo.pilot.dev.test`. Evidence: `~/.local/state/chart-infra/dns-pilot/macos-node-lookup-user-reported.json`. This verifies the hostname-resolution prerequisite for the standard-URI candidate. It does not verify TCP routing, TLS, Mongo membership, Compass or failover. The prior default SRV failure remains unresolved.

**Mac outage and recovery passed (Sean’s supplied output, 2026-10-01):** during the requested pilot outage, direct `dig @100.66.127.115` timed out, while `curl -I https://example.com` returned HTTP/2 200. The fresh public name `dns-pilot-1790835801.example.com` returned “No Such Record” through `dns-sd` at 14:23:21.629, about 31 ms after the displayed start time. The `0.0.0.0` displayed alongside that negative result is not a successful A record. After restoration, the fresh private name `check-1790835831.probe.pilot.dev.test` resolved to `100.66.127.115`. A subsequent PC-side check found the pilot Deployment ready at 1/1. Evidence: `~/.local/state/chart-infra/dns-pilot/macos-outage-user-reported.json`.

This passes the public DNS/HTTPS continuity and recovery check for the tested Mac configuration. Exact macOS/Tailscale versions and exit-node state were not supplied. It does not establish behaviour for every developer device, default Node SRV compatibility, or actual Mongo/Compass connectivity.

**Project extraction (2026-10-01):** DNS and Mongo tooling now live in `/home/sean/workspace/chart-infra`; old meta-workspace script paths forward there. New state/evidence is under `~/.local/state/chart-infra/`. Original shared-dev evidence remains as history. Existing Go infrastructure stays in its meta workspace.

## 1. On your Mac: test direct Tailscale DNS first

Connect Tailscale, then run:

```sh
dig @100.66.127.115 probe.pilot.dev.test A +short
dig @100.66.127.115 _mongodb._tcp.mongo.pilot.dev.test SRV +short
dig +tcp @100.66.127.115 _mongodb._tcp.mongo.pilot.dev.test SRV +short
dig @100.66.127.115 mongo.pilot.dev.test TXT +short
```

Expected A: `100.66.127.115`. Both SRV queries should return these three records, in any order:

```text
0 0 27017 member-0.mongo.pilot.dev.test.
0 0 27017 member-1.mongo.pilot.dev.test.
0 0 27017 member-2.mongo.pilot.dev.test.
```

Expected TXT: `"replicaSet=rs0&authSource=admin"`. If the direct queries time out, inspect the Mac’s Tailscale connection and tailnet access to both UDP/TCP port 53 before changing DNS settings.

## 2. Test a domain-specific resolver on Sean’s Mac

Sean is not a tailnet administrator, but confirmed local `sudo` access on the Mac. The tailnet-wide route is therefore not available to him. Use a Mac-only resolver for this pilot instead; it changes neither tailnet configuration nor the Mac’s default Wi-Fi DNS. Apple supports per-domain configuration files under `/etc/resolver/`. [Apple resolver manual](https://github.com/apple-oss-distributions/libresolv/blob/main/resolver.5)

Run **on the Mac**, not the PC:

```sh
sudo sh -c '
  set -euC
  mkdir -p /etc/resolver
  printf "%s\n" "# shared-dev DNS pilot" "nameserver 100.66.127.115" > /etc/resolver/pilot.dev.test
  chmod 644 /etc/resolver/pilot.dev.test
'
```

The no-clobber flag refuses to replace an existing file. If it reports that the file exists, inspect that file rather than overwriting it. The filename selects only `pilot.dev.test` and its subdomains. Keep Tailscale connected for reachability. Inspect `scutil --dns`, then use the native A/SRV tests below.

This is a test of the macOS scoped resolver, not proof that a Tailscale-admin-pushed rule or Compass’s DNS implementation works. Node’s DNS record-query APIs use a different path from OS hostname lookups, so native resolution success does not establish application SRV compatibility. Test the actual application resolver next; do not change the Mac’s global DNS to this PC to work around failures. [Node DNS behaviour](https://nodejs.org/api/dns.html#implementation-considerations)

If Node is installed, this optional diagnostic exercises its default SRV resolver without overriding DNS servers:

```sh
node -e 'require("node:dns").resolveSrv("_mongodb._tcp.mongo.pilot.dev.test", (e, r) => { console.log(e || r); process.exitCode = e ? 1 : 0; })'
```

Record a failure separately from native macOS results. A standalone Node version is not necessarily Compass’s bundled runtime. If application SRV cannot use the scoped resolver, retain the option of a standard three-member `mongodb://` URI and working member hostname resolution, or involve a tailnet/DNS administrator for centrally resolvable SRV records. No full-stack architecture change has been selected yet.

### Alternative when a tailnet administrator is available

After direct queries work, an administrator can open the Tailscale admin DNS page, Add nameserver → Custom:

| Setting | Pilot value |
| --- | --- |
| Nameserver | `100.66.127.115` |
| Restrict to domain / Split DNS | Enabled |
| Domain | `pilot.dev.test` (no wildcard) |

This is narrower than the final proposed `dev.test` zone. Leave global nameservers, MagicDNS, search domains and Override DNS servers unchanged. If a restricted entry for this suffix already exists, inspect it rather than replacing it blindly. Keep the Mac’s “Use Tailscale DNS settings” enabled. Include “Use with exit node” testing if developers actually use exit nodes. [Tailscale DNS](https://tailscale.com/docs/reference/dns-in-tailscale)

### Native macOS checks (for either configuration path)

Inspect the Mac resolver configuration:

```sh
scutil --dns
```

Then query a fresh synthetic hostname through macOS’s system resolver:

```sh
dscacheutil -q host -a name "check-$(date +%s).probe.pilot.dev.test"
```

It should return `100.66.127.115`. Test native SRV discovery too:

```sh
dns-sd -Q _mongodb._tcp.mongo.pilot.dev.test SRV
```

Wait for the three answers, then press Ctrl-C; `dns-sd` stays running by design. Direct `dig @...` proves the DNS server, not macOS split DNS. An ordinary `dig` can also differ from macOS’s scoped system resolver. The real Compass/driver DNS path must ultimately be exercised with the Mongo pilot, not inferred from these commands alone.

## 3. Verify the outage boundary

Keep a second terminal ready on the Mac. From the PC shell (or your existing SSH session), run:

```sh
cd /home/sean/workspace/chart-infra
python3 dns-pilot.py outage --seconds 30
```

Wait for `DNS pilot is DOWN`. The command stops only this pilot, waits 30 seconds and restores it automatically. During that window, on the Mac:

```sh
# Expected: query fails while the pilot is down.
dig @100.66.127.115 probe.pilot.dev.test A +time=2 +tries=1

# Expected: ordinary internet access still works.
curl -I --max-time 10 https://example.com

# Fresh public DNS query: expect an authoritative negative answer,
# rather than waiting for the unavailable development DNS server.
dns-sd -Q "dns-pilot-$(date +%s).example.com" A
```

For the last command, “No Such Record” is expected; stop it with Ctrl-C after the response. Also try a fresh `check-<timestamp>.probe.pilot.dev.test` lookup: it should fail or wait during the outage while public queries work independently. Avoid relying only on already-open browser tabs or cached names. After restoration, repeat the native hostname and SRV checks. Record the macOS/Tailscale/Compass versions, whether an exit node was active, and the observed results.

The helper restores the pilot in `finally` on normal errors, Ctrl-C or SIGTERM. A forcibly killed helper or lost PC cannot guarantee restoration; recover with `python3 dns-pilot.py up`. This drill does not stop k3s, Tailscale or the host.

## Rollback and stop

For the Mac-only path, remove only the resolver file created above, after confirming it still contains the pilot marker and expected server:

```sh
cat /etc/resolver/pilot.dev.test
sudo rm /etc/resolver/pilot.dev.test
```

Leave all other resolver files and network DNS settings alone. For the administrator-managed alternative, remove only the newly added restricted `pilot.dev.test` entry from the Tailscale DNS page when finished with the pilot. On the PC:

```sh
cd /home/sean/workspace/chart-infra
python3 dns-pilot.py down
```

`down` scales only the pilot to zero; its ConfigMap and manifest stay available. `up` restarts it; no source fetch, database mutation or live vendor traffic is involved. Remove a pilot DNS entry before retiring its server so the tailnet does not keep stale resolver configuration.

## Exact deployment and files

- Namespace: `shared-dev-dns-pilot`; Deployment and ConfigMap: `dns-pilot`.
- Kubernetes: `v1.36.3+k3s1`, node `sean-trontal`, Ubuntu 26.04.
- CoreDNS: `1.14.6`, `linux/amd64`, Go `1.26.5`, build `424d125`.
- Image: `docker.io/rancher/mirrored-coredns-coredns@sha256:900f9c109f7a33545d3c811516e8376df9019147b750f5ce3e254468769176ea` (already present in this cluster).
- Generator/lifecycle helper: `/home/sean/workspace/chart-infra/dns-pilot.py`.
- Applied manifest: `/home/sean/.local/state/shared-dev/dns-pilot/manifest.json`.
- Evidence: `/home/sean/.local/state/shared-dev/dns-pilot/before.json`, `verification.json`, `outage-host.json`.

The pilot uses host networking to bind only the Tailscale IPv4 address. That requires a dedicated namespace allowing host networking; developer namespaces stay at baseline. The container itself is non-root (UID/GID 65532), read-only, with privilege escalation disabled and only `NET_BIND_SERVICE`; no host directories, Kubernetes API token, Service, NodePort, LoadBalancer or PVC. CoreDNS permits sources in the Tailscale IPv4 range and refuses others. Host-network Pods are not isolated by ordinary Pod NetworkPolicy, so the binding, DNS ACL and tailnet/host network controls are part of the boundary. The pilot has no forwarding plugin and serves synthetic zone data only.

## Decision gate

Proceed with the DNS/SRV architecture only after the real Mac and actual application DNS resolver can resolve the records through the chosen configuration and ordinary internet access survives the outage. Native Mac-only resolver success must not be reported as tailnet-managed or Compass SRV success. Then prove the separately planned Mongo member advertisement, certificates, shared-port SNI routing and Compass election recovery. If those fail, adapt the DNS/endpoint design before building the full frontend stack.
