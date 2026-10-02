#!/bin/bash
set -euo pipefail
umask 077
cd /root/chart-prep
test "$(. /etc/os-release; printf '%s' "$VERSION_ID")" = 26.04
mountpoint -q /srv/chart/data
test -f /srv/chart/data/.chart-incus-box.json
test -c /dev/net/tun
test -f authorized_key
test "$#" -eq 6
chart_timezone=$1
shift
timedatectl set-timezone "$chart_timezone"

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
# The host supplies exact package=version arguments from versions.lock.json.
test "$#" -eq 5
apt-get update
apt-get install --yes --no-remove --no-install-recommends "$@" ./tailscale.deb
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
install -d -m 700 /srv/chart/data/identity/ssh /srv/chart/data/identity/tailscale /root/.ssh
for key in /etc/ssh/ssh_host_*; do
  test -f "$key" || continue
  name=$(basename "$key")
  if ! test -e "/srv/chart/data/identity/ssh/$name"; then
    cp -p "$key" "/srv/chart/data/identity/ssh/$name"
  fi
done
cat > /etc/ssh/sshd_config.d/00-chart.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
HostKey /srv/chart/data/identity/ssh/ssh_host_ed25519_key
HostKey /srv/chart/data/identity/ssh/ssh_host_rsa_key
EOF
if test -e /root/.ssh/authorized_keys; then
  cmp authorized_key /root/.ssh/authorized_keys
else
  install -m 600 authorized_key /root/.ssh/authorized_keys
fi
install -d /etc/systemd/system/tailscaled.service.d
cat > /etc/systemd/system/tailscaled.service.d/chart.conf <<'EOF'
[Unit]
RequiresMountsFor=/srv/chart/data
Requires=chart-input.service
After=chart-input.service
[Service]
ExecStart=
ExecStart=/usr/sbin/tailscaled --state=/srv/chart/data/identity/tailscale/tailscaled.state --socket=/run/tailscale/tailscaled.sock --port=41641
EOF
install -d -m 700 /srv/chart/data/datasets /srv/chart/data/backups
for chart_directory in /srv/chart/source /srv/chart/cache/pnpm; do
  if ! test -d "$chart_directory"; then
    install -d -m 700 "$chart_directory"
  fi
done
systemctl daemon-reload
systemctl enable --now chart-input.service
sshd -t
systemctl disable --now ssh.socket || true
systemctl enable --now ssh.service docker.service tailscaled.service
systemctl restart ssh.service tailscaled.service
# Package timers must not silently change the recorded preparation versions.
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
dpkg-query -W > /srv/chart/data/identity/packages.tsv
docker version
docker compose version
docker info --format 'DockerRootDir={{.DockerRootDir}} StorageDriver={{.Driver}}'
tailscale version
findmnt -T /var/lib/docker
findmnt -T /srv/chart/source
findmnt -T /srv/chart/data
touch /srv/chart/data/identity/guest-prepared
printf '%s\n' 'Guest prepared. Tailscale enrollment, Mutagen sessions and applications are separate steps.'
