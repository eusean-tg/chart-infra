# Connect to the Mongo pilot

The current pilot is a **single-member replica set** at **100.66.127.115:27017**, with password authentication. No Mongo TLS, CA certificate, custom DNS or SRV is required.

## 1. Connect Tailscale and copy the URI

On your Mac:

```sh
ssh sean@100.66.127.115 \
  'cat ~/.local/state/chart-infra/profiles/mongo-pilot-single/compass-uri.txt' | pbcopy
```

This copies the password-bearing URI directly to your clipboard. Do not paste it into chat or shared notes. It has this shape:

```text
mongodb://<user>:<password>@100.66.127.115:27017/chart?authSource=admin&directConnection=true
```

## 2. Save it in Compass

Choose **Add New Connection**, paste the URI and name it **Chart pilot**. Use a fresh saved connection so the retired pilot’s CA/TLS settings do not carry over. TLS should be off/unset; no certificate file is needed. Keep `directConnection=true` so Compass stays on the reachable IP:port instead of trying Kubernetes-only member names.

Open database `chart`, collection `persistence`. You should see `_id: "retained"` and `token: "single-member-fixture-v1"`.

Sean confirmed on 2026-10-01 that Compass on his Mac connects and displays `token: "single-member-fixture-v1"`. This verifies the client connection and read. Mac writes and the Compass version were not reported.

The account can read/write `chart` and cannot administer the replica set or another profile. For an optional client write check, insert a synthetic document into `chart.compass_check`, then read/edit it.

## 3. Remove the retired Mac DNS pilot entry

The CoreDNS pilot is stopped and removed. If you created its resolver file earlier, remove only that exact file after checking its contents:

```sh
cat /etc/resolver/pilot.dev.test
```

If it is the pilot file containing `# shared-dev DNS pilot` and `nameserver 100.66.127.115`:

```sh
sudo rm /etc/resolver/pilot.dev.test
```

Do not change global DNS or any other resolver files. The pilot CA file downloaded previously is unused; no trust-store installation was part of the walkthrough. API-hostname DNS is a separate future decision.

## Daily use on the PC

```sh
cd ~/workspace/chart-infra
./chart mongo status
./chart mongo down
./chart mongo up
```

For cleanup of runtime objects while preserving all data:

```sh
./chart mongo down --data-only
./chart mongo up
```

The saved Compass connection remains valid. Check `chart.persistence` again to see the same original record.

Inside Kubernetes, applications use the generated `internal-uri.txt`, which names the cluster member and sets `replicaSet=rs0`; they do not use the laptop URI. Application-specific users/databases will be provisioned when those services are implemented. This pilot’s user is scoped to `chart`.

The single member supports the verified transaction and change-stream tests but has no secondary or member failover. The frontend services themselves are not deployed yet.
