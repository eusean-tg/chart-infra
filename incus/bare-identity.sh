#!/bin/bash
set -euo pipefail
umask 077
cd /root/chart-prep
test "$(cat /var/lib/chart-bare-image)" = chart-bare-v1
mountpoint -q /srv/chart/data
test -f /srv/chart/data/.chart-incus-box.json
test -c /dev/net/tun
test -s authorized_keys
timedatectl set-timezone "$1"
install -d -m 700 /srv/chart/data/identity/ssh /srv/chart/data/identity/tailscale /root/.ssh
if test "${CHART_ADOPT_DATA:-0}" = 1; then
  test -s /srv/chart/data/identity/ssh/ssh_host_ed25519_key
  test -s /srv/chart/data/identity/ssh/ssh_host_rsa_key
  test -s /srv/chart/data/identity/tailscale/tailscaled.state
else
  test ! -e /srv/chart/data/identity/guest-prepared
  ssh-keygen -A
  cp -p /etc/ssh/ssh_host_* /srv/chart/data/identity/ssh/
fi
install -m 600 authorized_keys /root/.ssh/authorized_keys
install -m 600 authorized_keys /srv/chart/data/identity/authorized_keys
cat > /etc/ssh/sshd_config.d/00-chart.conf <<'SSH'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
HostKey /srv/chart/data/identity/ssh/ssh_host_ed25519_key
HostKey /srv/chart/data/identity/ssh/ssh_host_rsa_key
SSH
install -d /etc/systemd/system/tailscaled.service.d
cat > /etc/systemd/system/tailscaled.service.d/chart.conf <<'TS'
[Unit]
RequiresMountsFor=/srv/chart/data
Requires=chart-input.service
After=chart-input.service
[Service]
ExecStart=
ExecStart=/usr/sbin/tailscaled --state=/srv/chart/data/identity/tailscale/tailscaled.state --socket=/run/tailscale/tailscaled.sock --port=41641
TS
systemctl daemon-reload
systemctl enable --now chart-input.service
systemctl unmask ssh.service ssh.socket tailscaled.service
systemctl disable --now ssh.socket
sshd -t
systemctl enable --now ssh.service tailscaled.service docker.service
touch /srv/chart/data/identity/guest-prepared
ssh-keygen -lf /srv/chart/data/identity/ssh/ssh_host_ed25519_key.pub
