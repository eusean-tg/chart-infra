"""Inspect an exported unified image without extracting or booting its rootfs."""
from pathlib import PurePosixPath
import tarfile
import prep as p


def inspect(path, fingerprint):
    p.require(p.digest(path) == fingerprint, 'Export checksum differs from the published image fingerprint')
    required = {'rootfs/etc/machine-id': b'', 'rootfs/var/lib/chart-bare-image': b'chart-bare-v1\n'}
    forbidden = ('rootfs/root/chart-prep', 'rootfs/var/lib/chart-bare-builder',
                 'rootfs/root/.ssh/authorized_keys', 'rootfs/root/.bash_history',
                 'rootfs/root/.npmrc', 'rootfs/root/.netrc',
                 'rootfs/var/lib/tailscale/tailscaled.state', 'rootfs/srv/chart/data/identity')
    found = set(); source = False; seen = set()
    with tarfile.open(path, mode='r|*') as archive:
        for entry in archive:
            name = entry.name.removeprefix('./').rstrip('/')
            parts = PurePosixPath(name)
            p.require(not parts.is_absolute() and '..' not in parts.parts, 'Unsafe image archive path')
            p.require(name not in seen, 'Duplicate image archive path: ' + name); seen.add(name)
            p.require(not any(name == f or name.startswith(f + '/') for f in forbidden),
                      'Image contains a provisioning/identity artifact: ' + name)
            p.require(not name.startswith('rootfs/etc/ssh/ssh_host_'), 'Image contains an SSH host key')
            p.require(not name.startswith('rootfs/srv/chart/source/'), 'Image contains application source')
            if name == 'rootfs/srv/chart/source': source = entry.isdir()
            if name in required:
                p.require(entry.isfile() and entry.size <= 128, 'Expected regular image marker: ' + name)
                p.require(archive.extractfile(entry).read() == required[name], 'Image marker content differs: ' + name)
                found.add(name)
    p.require(found == set(required) and source, 'Image lacks expected bare-rootfs markers')
    return {'sha256': fingerprint, 'machine_id': 'empty', 'ssh_host_keys': 'absent',
            'provisioning_and_identity_artifacts': 'absent', 'source': 'empty', 'extracted': False}
