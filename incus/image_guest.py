#!/usr/bin/env python3
"""Prepare portable image artifacts, sanitize a clean builder, or adopt a seed in a new box."""
import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import socket
import sys
import tempfile

import backing as b
import box

SEED = Path('/var/lib/chart-seed')
BUILD_MARKER = Path('/var/lib/chart-image-builder')
SEED_OWNER = 'chart-incus-seed-v1'


def builder():
    b.require(os.geteuid() == 0 and BUILD_MARKER.is_file(), 'Not a registered image builder')
    marker = b.read(BUILD_MARKER)
    b.require(marker['owner'] == SEED_OWNER and marker['name'] == socket.gethostname(), 'Wrong builder identity')
    b.require(not (b.ROOT / 'identity').exists(), 'Developer/acceptance identity forbidden in builder')
    mounts = json.loads(b.run(['findmnt', '-J', '-o', 'TARGET']))
    b.require('/srv/chart/data' not in json.dumps(mounts), 'Builder must not attach retained HDD data')
    return marker


def scan_known_credentials(roots, needles):
    """Scan regular files without following links or printing matching contents."""
    seen = set()
    for root in roots:
        for here, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(here) / d).is_symlink()]
            for name in files:
                path = Path(here) / name
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                tail = b''
                with path.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        data = tail + chunk
                        b.require(not any(n in data for n in needles), f'Credential material in image artifact: {path}')
                        tail = data[-max(len(n) for n in needles):]


def artifact_manifest(build, inputs):
    sources = box.workspace('baseline')
    deps = {s: box.dependency('baseline', s) for s in box.SERVICES}
    for service, dep in deps.items():
        b.require(dep['labels'] == {b.LABEL: SEED_OWNER, 'chart-infra.build': build['build'],
                  'chart-infra.service': service}, 'Dependency artifact provenance differs')
        dep.pop('log', None)
    return {'schema': 1, 'owner': SEED_OWNER, 'build': build['build'], 'commit': build['commit'],
            'source_filter': 'chart-image-v1',
            'architecture': 'amd64', 'workspace': 'baseline', 'runtime': box.runtime(),
            'sources': sources, 'dependencies': deps, 'pins': inputs['pins'],
            'backing_images': b.IMAGES, 'packages_sha256': b.digest(Path('/var/lib/chart-image-packages.tsv'))}


def source_credentials(path):
    private_key = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----(?:\r?\n|\\n)[A-Za-z0-9+/=]{32}')
    registry_token = re.compile(rb'(?m)^\s*(?://[^\s]+/)?_authToken\s*=\s*[^\s$]{20,}')
    for relative in box.source_policy.manifest(path):
        if relative.casefold() in box.source_policy.TEMPLATES:
            continue
        data = (path / relative).read_bytes()
        b.require(not private_key.search(data) and not registry_token.search(data),
                  'Credential-like material in seeded source: ' + relative)


def prepare():
    marker = builder()
    b.require(not SEED.exists(), 'Seed state exists; inspect failed builder, do not overwrite it')
    SEED.mkdir(mode=0o755)
    box.STATE = SEED / 'receipts'
    box.STATE.mkdir(mode=0o700)
    inputs = b.read(Path('/root/chart-image/inputs.json'))
    box.register_runtime(inputs['runtime']['image'])
    for service, (repo, _, _) in box.SERVICES.items():
        record = inputs['sources'][repo]
        box.stage('baseline', service, '/root/chart-image/' + record['archive'], record['sha256'], record['revision'])
        source_credentials(box.SOURCE / 'baseline' / repo)
    # stdin contains only the approved npmjs registry line; it never enters an image layer.
    value = sys.stdin.read()
    with tempfile.TemporaryDirectory(prefix='chart-image-auth-', dir='/run') as temporary:
        path = Path(temporary) / 'npmrc'
        b.write(path, value)
        _, token = box.npm_token(path)
        for service in box.SERVICES:
            box.install_deps('baseline', service, 'seed', npmrc_path=path, artifact_labels={
                b.LABEL: SEED_OWNER, 'chart-infra.build': marker['build'], 'chart-infra.service': service})
        scan_known_credentials([Path('/etc'), Path('/root'), Path('/var/lib'), Path('/var/log'), Path('/srv')], [token.encode()])
    manifest = artifact_manifest(marker, inputs)
    b.save(SEED / 'manifest.json', manifest)
    print('Portable source and dependency artifacts prepared; no application identity or data created.')


def validate_manifest():
    manifest = b.read(SEED / 'manifest.json')
    b.require(manifest['schema'] == 1 and manifest['owner'] == SEED_OWNER
              and manifest['architecture'] == 'amd64' and manifest['workspace'] == 'baseline'
              and manifest['source_filter'] == 'chart-image-v1', 'Unexpected seed format')
    b.require(set(manifest['sources']) == set(box.SERVICES) and set(manifest['dependencies']) == set(box.SERVICES),
              'Incomplete seed artifact')
    b.require(manifest['backing_images'] == b.IMAGES, 'Backing image pins differ')
    b.require(manifest['packages_sha256'] == b.digest(Path('/var/lib/chart-image-packages.tsv')),
              'Seed package inventory differs')
    for service, (repo, _, _) in box.SERVICES.items():
        src = manifest['sources'][service]
        path = b.safe(box.SOURCE / 'baseline' / repo)
        source_credentials(path)
        b.require(src['kind'] == 'bundle' and src['path'] == str(path)
                  and src['revision'] == manifest['pins']['repos'][repo]['sha'], 'Seed source provenance differs')
        b.require(box.source_policy.fingerprint(box.source_policy.manifest(path)) == src['fingerprint'], 'Seed source changed')
        dep = manifest['dependencies'][service]
        b.require(dep['image'] == manifest['runtime']['image'] and dep['key'] == box.key(path, dep['image']), 'Seed dependency inputs differ')
        b.require(dep['labels'] == {b.LABEL: SEED_OWNER, 'chart-infra.build': manifest['build'],
                  'chart-infra.service': service}, 'Seed dependency labels differ')
        obj = box.volume(dep['volume'], dep['labels'])
        b.require((b.safe(Path(obj['Mountpoint'])) / '.chart-installed').read_text() == dep['key'], 'Incomplete seed dependencies')
    loaded = json.loads(b.run(['docker', 'image', 'inspect', manifest['runtime']['image']]))[0]
    b.require(loaded['Id'] == manifest['runtime']['image'] and loaded['Architecture'] == 'amd64'
              and loaded['Config']['User'] == '1000:1000', 'Seed runtime identity differs')
    for image in b.IMAGES.values():
        b.run(['docker', 'image', 'inspect', image])
    return manifest


def seal():
    builder()
    m = validate_manifest()
    b.require(not b.run(['docker', 'ps', '-aq']), 'Builder contains containers; inspect before publication')
    names = set(b.run(['docker', 'volume', 'ls', '-q']).split())
    b.require(names == {d['volume'] for d in m['dependencies'].values()}, 'Unexpected builder volumes')
    b.require(not (b.ROOT / 'identity').exists(), 'Private identity in builder')
    for path in [Path('/var/lib/tailscale/tailscaled.state'), Path('/root/.npmrc'), Path('/root/.ssh/authorized_keys')]:
        b.require(not path.exists(), f'Unexpected private builder state: {path}')
    # Remove only disposable builder metadata/OS identities; artifacts and packages remain.
    b.run(['systemctl', 'stop', 'docker.service', 'docker.socket', 'containerd.service'])
    for directory in ['/var/log', '/var/lib/cloud', '/var/lib/dhcp']:
        path = Path(directory)
        if path.exists():
            for child in path.iterdir():
                if child.is_dir() and not child.is_symlink(): shutil.rmtree(child)
                else: child.unlink()
    for path in [*Path('/etc/ssh').glob('ssh_host_*'), Path('/var/lib/systemd/random-seed'),
                 Path('/var/lib/docker/key.json'), Path('/var/lib/docker/engine-id'),
                 Path('/root/.bash_history'), Path('/root/.python_history')]:
        if path.exists(): path.unlink()
    Path('/etc/machine-id').write_text('')
    dbus = Path('/var/lib/dbus/machine-id')
    if dbus.exists() or dbus.is_symlink(): dbus.unlink()
    dbus.symlink_to('/etc/machine-id')
    shutil.rmtree('/root/chart-image')
    shutil.rmtree(SEED / 'receipts')
    BUILD_MARKER.unlink()
    b.write(SEED / 'sealed', b.digest(SEED / 'manifest.json') + '\n', mode=0o444)
    print('Builder sanitized and Docker stopped; stop the Incus instance before publishing.')


def adopt(box_name):
    marker = b.guard(box_name)
    b.inventory(box_name, marker)
    b.ownership(box_name)
    b.require(not box.containers(), 'Seed adoption requires a box with no app containers')
    b.require((SEED / 'sealed').read_text().strip() == b.digest(SEED / 'manifest.json'), 'Unsealed/changed seed manifest')
    manifest = validate_manifest()
    receipt = box.STATE / 'image-seed.json'
    expected = {'build': manifest['build'], 'manifest_sha256': b.digest(SEED / 'manifest.json'), 'box': box_name}
    if receipt.exists():
        b.require(b.read(receipt) == expected, 'Different seed already adopted')
        for s in box.SERVICES: box.dependency('baseline', s)
        print('Matching seed adoption retained.')
        return
    b.require(not box.STATE.exists(), 'Existing app state requires explicit retained-data adoption, not fresh seed adoption')
    box.registry(box_name, create=True)
    b.save(box.STATE / 'runtime.json', manifest['runtime'])
    (box.STATE / 'sources').mkdir(mode=0o700)
    b.save(box.STATE / 'sources/baseline.json', manifest['sources'])
    directory = box.STATE / 'dependencies/baseline'
    directory.mkdir(parents=True, mode=0o700)
    for service, record in manifest['dependencies'].items(): b.save(directory / (service + '.json'), record)
    b.save(receipt, expected)
    print('Prepared image source/dependencies adopted; config, app start and registry access remain separate.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'seal', 'adopt'])
    p.add_argument('--box')
    a = p.parse_args()
    os.umask(0o077)
    b.require(os.geteuid() == 0, 'Run inside the selected box as root')
    with open('/run/lock/chart-backing.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.action == 'adopt': adopt(a.box)
        else: globals()[a.action]()


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
