#!/usr/bin/env python3
"""Opt-in stopped backup, scratch restore and identity recreation on one image test box."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import prep as p
import host_common as h
import image
import boxes


FIXTURE = '''
import hashlib, json, os, pathlib, stat, sys
root = pathlib.Path(sys.argv[1])
if sys.argv[2] == 'create':
    root.mkdir(mode=0o755)
    for name, uid in [('root.txt', 0), ('developer.txt', 1000)]:
        path = root / name
        with path.open('x') as f: f.write('bare-box-recovery-fixture-v1:' + name)
        os.chown(path, uid, uid)
        path.chmod(0o600)
result = {}
for name in ('root.txt', 'developer.txt'):
    path = root / name; info = path.stat()
    result[name] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                       uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode))
print(json.dumps(result))
'''


def run(a):
    c = p.config(a.config); p.check_host(c)
    _, tests = image.names(a.build)
    name = tests[0]
    if not a.apply:
        print(json.dumps({'test_box': name, 'operations': ['create synthetic HDD files',
            'stopped NVMe backup and rootfs export', 'scratch restore', 'retain original rootfs',
            'recreate with original HDD and tailnet identity', 'verify data and numeric ownership'],
            'cleanup': 'retain fixtures, copies and original stopped rootfs; no new tailnet enrollment'}, indent=2))
        return
    with p.locked():
        h.host(c)
        build = json.loads((p.STATE / 'bare-images' / a.build / 'build.json').read_text())
        p.require(build.get('owner') == h.OWNER and build.get('tests') == tests and build.get('verified'),
                  'Select a verified build with its original test-box names')
        fingerprint = build['fingerprint']
        scripts = boxes.scripts(c, fingerprint)
        obj = h.owned(c, name)
        p.require(obj['status'] == 'Running' and obj['config'].get('volatile.base_image') == fingerprint,
                  'Expected the running original image test box')
        evidence = p.STATE / 'recovery-proofs' / a.build
        p.require(not evidence.exists(), 'Recovery proof exists; inspect its phase before another attempt')
        fixture = '/srv/chart/data/recovery-proof-' + str(time.time_ns())
        record = {'owner': h.OWNER, 'build': a.build, 'box': name, 'image': fingerprint,
                  'fixture': fixture, 'phase': 'fixture-preparation'}
        h.save(evidence / 'proof.json', record)
        record['before'] = json.loads(h.guest(c, name, ['python3', '-c', FIXTURE, fixture, 'create']))
        record['phase'] = 'recreating'; h.save(evidence / 'proof.json', record)
        result = boxes.recreate(c, name, fingerprint, scripts)
        record['recreation'] = result; h.save(evidence / 'proof.json', record)
        record['after'] = json.loads(h.guest(c, name, ['python3', '-c', FIXTURE, fixture, 'read']))
        p.require(record['before'] == record['after'], 'HDD file contents, guest UID/GID or modes differ after recreation')
        p.require(h.instance(c, result['retained_instance'])['status'] == 'Stopped', 'Original rootfs must remain stopped')
        record['phase'] = 'verified'; h.save(evidence / 'proof.json', record)
        print(json.dumps({'verified_recovery': name, 'image': fingerprint, 'backup': result['backup'],
                          'scratch': result['restore_check']['scratch'], 'retained_instance': result['retained_instance'],
                          'evidence': str(evidence / 'proof.json'), 'tailnet_identity': 'preserved; no enrollment needed'}, indent=2))


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True); parser.add_argument('--build', required=True)
    parser.add_argument('--apply', action='store_true')
    try: run(parser.parse_args())
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
