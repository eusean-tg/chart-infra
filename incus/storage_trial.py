#!/usr/bin/env python3
"""Compare synthetic nested-Docker/storage behavior before an HDD pool migration."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import prep as p
import host_common as h

OWNER = 'chart-storage-trial-v1'
POOL = 'hdd'


def target(c):
    path = Path(c['hdd_mount']) / 'shared-dev/incus'
    p.no_symlinks(path)
    return path


def trial_names(value):
    p.require(re.fullmatch(r'[a-z][a-z0-9-]{0,11}', value), 'Use a trial ID of 1–12 lowercase letters/digits/hyphens')
    return ['chart-storage-test-' + value + '-' + medium for medium in ('ssd', 'hdd')]


def pool_spec(c):
    return {'driver': 'dir', 'config': {'source': str(target(c)), 'user.chart-infra': p.OWNER}}


def validate_pool(c, obj):
    wanted = pool_spec(c)
    p.require(obj.get('driver') == wanted['driver'], 'HDD pool driver differs')
    for key, value in wanted['config'].items():
        p.require(obj.get('config', {}).get(key) == value, 'HDD pool configuration differs: ' + key)
    p.require(target(c).is_dir() and target(c).stat().st_uid == 0, 'HDD pool source must be a root-owned directory')
    p.require(target(c).stat().st_dev == Path(c['hdd_mount']).stat().st_dev, 'Pool source is not on the registered HDD')


def fixture_spec(c, name, pool, fingerprint, trial):
    spec = p.instance_spec(c, name)
    spec['source']['fingerprint'] = fingerprint
    spec['devices'].pop('data'); spec['devices'].pop('tun')
    spec['devices']['root']['pool'] = pool
    spec['config']['user.chart-storage-trial'] = trial
    return spec


def validate_fixture(c, name, expected):
    obj = h.instance(c, name)
    p.require(obj.get('profiles') == [] and obj.get('devices') == expected['devices'], 'Trial devices/profiles differ; inspect before cleanup')
    for key, value in expected['config'].items():
        p.require(obj.get('config', {}).get(key) == value, 'Trial configuration differs: ' + key)
    p.require(all(key in expected['config'] or key.startswith(('image.', 'volatile.')) for key in obj['config']),
              'Unrecognized trial configuration; inspect before cleanup')
    p.require(obj['config'].get('volatile.base_image') == expected['source']['fingerprint'], 'Trial image differs')
    return obj


BENCH = r'''
import hashlib,json,os,pathlib,time
root=pathlib.Path('/root/chart-storage-fixture'); root.mkdir()
payload=b'chart-storage-fixture\n'*195
start=time.monotonic()
for group in range(32):
    directory=root/str(group); directory.mkdir()
    for entry in range(128):
        with (directory/str(entry)).open('wb') as f: f.write(payload)
# Flush only this fixture's files; no host-wide sync or cache eviction.
for path in root.glob('*/*'):
    with path.open('rb') as f: os.fsync(f.fileno())
write=time.monotonic()-start
start=time.monotonic(); digest=hashlib.sha256(); count=0
for path in sorted(root.glob('*/*')):
    digest.update(path.read_bytes()); count+=1
scan=time.monotonic()-start
result={'files':count,'bytes':count*len(payload),'write_and_fsync_seconds':write,
        'cached_scan_seconds':scan,'sha256':digest.hexdigest()}
(root/'result.json').write_text(json.dumps(result))
print(json.dumps(result))
'''
SMOKE = r'''
set -eu
systemctl start docker
systemctl is-active --quiet docker
docker info >/dev/null
mkdir /root/chart-docker-fixture
cd /root/chart-docker-fixture
printf 'int main(void) { return 0; }\n' > check.c
gcc -static check.c -o check
printf 'FROM scratch\nCOPY check /check\nENTRYPOINT ["/check"]\n' > Dockerfile
docker build --network=none -t chart-storage-fixture . >/dev/null
docker run --name chart-storage-fixture --network=none chart-storage-fixture
'''


def execute(c, trial, fingerprint):
    h.host(c)
    h.candidate(c, fingerprint, verified=True)
    p.require(c['pool'] != POOL, 'This comparison requires an existing separate source pool')
    names = trial_names(trial)
    receipt = p.STATE / 'storage-trials' / trial
    p.no_symlinks(receipt)
    p.require(not receipt.exists(), 'Trial receipt exists; inspect retained resources before another run')
    existing = p.query('/1.0/instances?project=' + c['project'] + '&recursion=1')
    p.require(not any(x['name'] in names for x in existing), 'Trial instance name collision')
    p.require(not p.capacity(c)['review'], 'Source pool/SSD capacity needs review')
    disk = shutil.disk_usage(c['hdd_mount'])
    p.require(disk.free > max(10 * 1024**3, disk.total * .15), 'Keep 15% HDD free and at least 10 GiB for trial')
    pools = p.query('/1.0/storage-pools?recursion=1')
    selected = next((x for x in pools if x['name'] == POOL), None)
    if selected is None:
        p.require(not target(c).exists(), 'Unregistered HDD pool directory exists; refuse adoption')
    else:
        validate_pool(c, selected)
    record = {'owner': OWNER, 'host': c['machine_id'], 'trial': trial, 'image': fingerprint,
              'pool': POOL, 'source': str(target(c)), 'phase': 'preparing', 'results': {}}
    h.save(receipt / 'proof.json', record)
    if selected is None:
        target(c).mkdir(mode=0o700)
        p.query('/1.0/storage-pools', {'name': POOL, **pool_spec(c)}, 'POST')
        validate_pool(c, p.query('/1.0/storage-pools/' + POOL))
    for name, pool in zip(names, (c['pool'], POOL)):
        spec = fixture_spec(c, name, pool, fingerprint, trial)
        record['phase'] = 'creating-' + name; h.save(receipt / 'proof.json', record)
        p.query('/1.0/instances?project=' + c['project'], spec, 'POST')
        try:
            validate_fixture(c, name, spec)
            start = time.monotonic()
            p.run(['incus', 'start', 'local:' + name, '--project', c['project']])
            h.wait_ready(c, name)
            boot = time.monotonic() - start
            # The bare image has SSH and Tailscale masked; no credentials or enrollment are supplied.
            p.require(not h.guest(c, name, ['sh', '-c', 'find /srv/chart/source -mindepth 1 -print -quit']).strip(), 'Image has source content')
            result = json.loads(h.guest(c, name, ['python3', '-c', BENCH]))
            result['boot_seconds'] = boot
            h.guest(c, name, ['timeout', '180', 'bash', '-c', SMOKE])
            p.run(['incus', 'stop', 'local:' + name, '--project', c['project'], '--timeout', '120'])
            p.run(['incus', 'start', 'local:' + name, '--project', c['project']])
            h.wait_ready(c, name)
            retained = json.loads(h.guest(c, name, ['cat', '/root/chart-storage-fixture/result.json']))
            p.require(retained['sha256'] == result['sha256'], 'Fixture receipt changed across restart')
            actual = h.guest(c, name, ['python3', '-c', "import hashlib,pathlib; h=hashlib.sha256(); [h.update(p.read_bytes()) for p in sorted(pathlib.Path('/root/chart-storage-fixture').glob('*/*'))]; print(h.hexdigest())"]).strip()
            p.require(actual == result['sha256'], 'Fixture files differ after restart')
            h.guest(c, name, ['bash', '-ec', 'systemctl start docker; docker start -a chart-storage-fixture'])
            result.update(docker='build/run/restart passed', file_retention='passed')
            record['results'][pool] = result
            record['phase'] = 'verified-' + name; h.save(receipt / 'proof.json', record)
        finally:
            obj = validate_fixture(c, name, spec)
            if obj['status'] == 'Running':
                p.run(['incus', 'stop', 'local:' + name, '--project', c['project'], '--timeout', '120'])
        # Only successful synthetic instances created by this run are removed.
        p.require(validate_fixture(c, name, spec)['status'] == 'Stopped', 'Trial did not stop')
        p.run(['incus', 'delete', 'local:' + name, '--project', c['project']])
    record.update(phase='verified', scope='synthetic cached-file scan, file writes, Docker build/run and restart; not application performance')
    h.save(receipt / 'proof.json', record)
    print(json.dumps({**record, 'evidence': str(receipt / 'proof.json'),
                      'deployment': 'unchanged; HDD pool retained for reviewed migration'}, indent=2))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--trial', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--apply', action='store_true')
    a = parser.parse_args(); c = p.config(a.config); p.check_host(c)
    names = trial_names(a.trial)
    p.require(re.fullmatch('[0-9a-f]{64}', a.image), 'Supply a full verified image fingerprint')
    print(json.dumps({'pool': POOL, 'source': str(target(c)), 'driver': 'dir', 'fixtures': names,
                      'tailscale_enrollment': False, 'existing_instances': 'untouched',
                      'cleanup': 'remove successful synthetic instances; retain pool, cached image and proof; failures retained',
                      'apply': a.apply}, indent=2), flush=True)
    if a.apply:
        with p.locked(): execute(c, a.trial, a.image)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print('Refused:', error, file=sys.stderr); sys.exit(1)
