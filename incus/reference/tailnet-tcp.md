# Expose a box-local TCP service over Tailscale

Publish Mongo from Docker to `127.0.0.1:27017`. For laptop access, create a socket
and matching service inside the developer's box. Replace `100.64.0.10` with that
box's actual Tailscale IPv4; never bind the proxy to all interfaces. These examples
assume Docker's default bridge NAT mode and the prepared guest input firewall.
Keep routing/firewall changes explicit if choosing a different Docker network mode.

`/etc/systemd/system/dev-mongo.socket`:

```ini
[Unit]
Description=Developer Mongo on the box Tailscale address
Requires=chart-input.service
After=chart-input.service

[Socket]
ListenStream=100.64.0.10:27017
FreeBind=yes

[Install]
WantedBy=sockets.target
```

`/etc/systemd/system/dev-mongo.service`:

```ini
[Unit]
Description=Forward developer Mongo to loopback
Requires=dev-mongo.socket
After=dev-mongo.socket

[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd 127.0.0.1:27017
DynamicUser=yes
PrivateTmp=yes
```

`FreeBind=yes` permits socket creation before the Tailscale IP is assigned; it
does not make the backend ready or change routing. Verify the proxy executable
exists. Retain unit copies under `/srv/chart/data/private/systemd/`, install them
with mode 0644, then run `systemctl daemon-reload` and
`systemctl enable --now dev-mongo.socket`. The matching service starts on demand.
Inspect `ss -ltn`, both unit journals, authenticated Mongo access from the laptop,
and recovery after a coordinated box reboot. Use `directConnection=true` remotely.

Mongo still requires authentication. Access is controlled by the guest input
firewall and tailnet ACLs. Dragonfly can remain loopback-only when all its clients
are inside the box. Do not expose an extra service merely because an example exists.

Reference: installed `systemd.socket(5)` (`FreeBind=`) and
`systemd-socket-proxyd(8)`. Docker's
[published-port rules](https://docs.docker.com/engine/network/port-publishing/)
and [firewall handling](https://docs.docker.com/engine/network/packet-filtering-firewalls/)
are separate from the guest's input chain.
