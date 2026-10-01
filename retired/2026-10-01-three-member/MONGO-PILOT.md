# Connect to the Mongo pilot from your Mac

This is an isolated development replica set with synthetic data. All three members are on Sean’s PC. The PC-side checks passed; the actual Mac Compass connection and election check are the remaining acceptance step.

## 1. Connect Tailscale

Keep the `/etc/resolver/pilot.dev.test` configuration used in the DNS tests. No new global DNS setting or tailnet-admin access is needed for this pilot.

## 2. Download the public CA certificate

On your Mac:

```sh
mkdir -p "$HOME/.config/chart-infra/pilot"
scp sean@100.66.127.115:/home/sean/.local/state/chart-infra/mongo-pilot/ca.crt \
  "$HOME/.config/chart-infra/pilot/ca.crt"
```

This is a public trust certificate, not a private key. You select it for this Compass connection; system-wide certificate installation is unnecessary. The certificate’s SHA-256 fingerprint is:

```text
0D:23:AD:D0:1E:7B:32:A4:DD:C8:3C:34:51:4C:DE:FE:88:74:24:2E:F4:5D:C7:4E:92:FF:24:DB:47:D8:E5:17
```

## 3. Copy your connection string to the Mac clipboard

```sh
ssh sean@100.66.127.115 \
  'cat ~/.local/state/chart-infra/mongo-pilot/compass-uri.txt' | pbcopy
```

The generated URI includes the pilot-only password. Paste it into Compass, not chat or shared notes. It lists the three names on port 27017, sets `replicaSet=rs0`, `authSource=admin`, and enables TLS. No SRV lookup is used.

## 4. Save the Compass connection

1. In Compass, choose **Add New Connection** and paste the URI.
2. Open **Advanced Connection Options → TLS / SSL** and set TLS **On**.
3. For **Certificate Authority**, select `~/.config/chart-infra/pilot/ca.crt`. In the Mac file picker, use **Cmd-Shift-G** to enter that path if the hidden folder is not shown.
4. Leave the client certificate empty and certificate/hostname validation enabled. This pilot uses password authentication over TLS.
5. Name the connection **Chart pilot** and save/connect.

The CA field and TLS controls are described in [MongoDB’s Compass documentation](https://www.mongodb.com/docs/compass/connect/advanced-connection-options/tls-ssl-connection/). Do not substitute the raw Tailscale IP for member names: TLS routing needs the hostname.

Open database `pilot`, collection `persistence`. You should see `_id: "retained"` with `token: "synthetic-fixture-v1"`. The pilot account can read/write `pilot` but cannot administer the replica set or access unrelated databases.

## 5. Check writes and election recovery

Use Compass to insert a synthetic document in `pilot.compass_check`, for example:

```json
{"_id":"mac-check","message":"connected from Compass"}
```

While Compass remains connected, run this from your Mac’s terminal:

```sh
ssh sean@100.66.127.115 \
  'cd ~/workspace/chart-infra && ./chart mongo step-down'
```

Refresh the collection and edit that document. A brief reconnect is expected; the saved connection should discover the new primary. Report connection/write results and your Compass version. The PC already passed the equivalent driver test, but that does not establish Compass behaviour on your Mac.

## Daily use and stop/start

On the PC, `cd ~/workspace/chart-infra` and use:

```sh
./chart mongo status
./chart mongo down
./chart mongo up
```

For runtime cleanup while retaining all data:

```sh
./chart mongo down --data-only
./chart mongo up
```

The same saved Compass connection and credentials continue to apply. DNS runs independently. There is no data-delete command. Do not delete the HDD identity directory, member directories, PV or PVC.

This pilot does not yet deploy auth-service, tharamine, Orange, source sync or developer accounts. Its fixed `default` dataset proves retention; selecting other named datasets is still future lifecycle work.
