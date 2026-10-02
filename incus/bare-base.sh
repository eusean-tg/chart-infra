#!/bin/bash
set -euo pipefail
umask 077
cd /root/chart-prep
test -f /var/lib/chart-bare-builder
test ! -e /srv/chart/data/identity
test "$(. /etc/os-release; printf '%s' "$VERSION_ID")" = 26.04
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
apt-get update
apt-get install --yes --no-remove --no-install-recommends "$@" ./tailscale.deb
rm /usr/sbin/policy-rc.d
systemctl mask --now ssh.service ssh.socket tailscaled.service
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
install -d -m 755 /opt/nvm /srv/chart/source
install -m 644 nvm.sh bash_completion /opt/nvm/
install -m 755 nvm-exec /opt/nvm/
cat > /etc/profile.d/chart-nvm.sh <<'NVM'
export NVM_DIR=/opt/nvm
. "$NVM_DIR/nvm.sh"
NVM
# Interactive root shells and explicit noninteractive SSH commands can load nvm.
printf '\n. /etc/profile.d/chart-nvm.sh\n' >> /root/.bashrc
. /etc/profile.d/chart-nvm.sh
test "$(nvm --version)" = 0.40.3
systemctl enable --now docker.service
docker info >/dev/null
test -z "$(docker ps -aq)"
test -z "$(docker image ls -q)"
test -z "$(docker volume ls -q)"
test -z "$(ls -A /srv/chart/source)"
dpkg-query -W > /var/lib/chart-bare-packages.tsv
# Install the default guest input firewall without starting SSH or enrolling Tailscale.
bash /root/chart-prep/guest-firewall.sh
systemctl stop docker.service docker.socket containerd.service
rm -f /etc/ssh/ssh_host_* /var/lib/systemd/random-seed /var/lib/docker/key.json /var/lib/docker/engine-id
rm -f /root/.bash_history
rm -rf /var/log/* /var/lib/cloud/* /var/lib/dhcp/*
printf '' > /etc/machine-id
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id
test ! -e /var/lib/tailscale/tailscaled.state
test ! -e /root/.ssh/authorized_keys
rm -rf /root/chart-prep
rm /var/lib/chart-bare-builder
printf 'chart-bare-v1\n' > /var/lib/chart-bare-image
