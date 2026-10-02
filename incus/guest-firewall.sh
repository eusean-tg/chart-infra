#!/bin/bash
set -euo pipefail
# Guest firewall is independent of Docker's forwarding chains.
cat > /etc/chart-input.nft <<'EOF'
table inet chart_input {
  chain input {
    type filter hook input priority -10; policy drop;
    iifname "lo" accept
    iifname "tailscale0" accept
    ct state established,related accept
    udp sport 67 udp dport 68 accept
    udp dport 41641 accept
    meta l4proto tcp reject with tcp reset
    reject with icmpx type admin-prohibited
  }
}
EOF
cat > /usr/local/sbin/chart-input <<'EOF'
#!/bin/sh
set -eu
if nft list table inet chart_input >/dev/null 2>&1; then
  { printf 'delete table inet chart_input\n'; cat /etc/chart-input.nft; } | nft -f -
else
  nft -f /etc/chart-input.nft
fi
EOF
chmod 755 /usr/local/sbin/chart-input
cat > /etc/systemd/system/chart-input.service <<'EOF'
[Unit]
Description=Chart box Tailscale-only input
Before=ssh.service ssh.socket tailscaled.service docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/chart-input
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now chart-input.service
