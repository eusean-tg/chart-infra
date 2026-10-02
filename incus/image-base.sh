#!/bin/bash
set -euo pipefail
umask 077
cd /root/chart-image
# This script is installed only into a clean, operator-created image builder.
test -f /var/lib/chart-image-builder
test ! -e /srv/chart/data/identity
test "$(. /etc/os-release; printf '%s' "$VERSION_ID")" = 26.04
test "$#" -eq 5
# Prevent package postinst from starting SSH or an unenrolled Tailscale daemon.
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
apt-get update
apt-get install --yes --no-remove --no-install-recommends "$@" ./tailscale.deb
rm /usr/sbin/policy-rc.d
systemctl mask --now ssh.service ssh.socket tailscaled.service
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
install -d -m 755 /srv/chart/source /srv/chart/cache/pnpm /opt/chart-infra/incus
systemctl enable --now docker.service
# Package/SSH identities are sanitized before this builder is published.
dpkg-query -W > /var/lib/chart-image-packages.tsv
