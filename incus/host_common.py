"""Host-only ownership, metadata and Incus transport for personal boxes."""
import json
import os
from pathlib import Path
import time
import prep as p

OWNER = 'chart-bare-v1'


def save(path, value):
    path = Path(path); p.no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.tmp-' + str(time.time_ns()))
    with temporary.open('x') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(value, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    temporary.replace(path)


def guest(c, name, args, stdin=None):
    return p.run(['incus', 'exec', 'local:' + name, '--project', c['project'], '--', *args], input=stdin)


def push(c, name, source, dest):
    p.run(['incus', 'file', 'push', source, 'local:' + name + dest, '--project', c['project']])


def instance(c, name):
    p.box_name(name)
    return p.query('/1.0/instances/' + name + '?project=' + c['project'])


def owned(c, name):
    obj = instance(c, name)
    spec = p.instance_spec(c, name)
    p.require(obj['devices'] == spec['devices'] and obj['profiles'] == [], 'Foreign box devices/profiles')
    for k, v in spec['config'].items():
        p.require(obj['config'].get(k) == v, 'Box configuration differs: ' + k)
    if 'user.chart-box' in obj['config']:
        p.require(obj['config']['user.chart-box'] == OWNER, 'Unexpected personal-box registration')
        spec['config']['user.chart-box'] = OWNER
    p.require(all(k in spec['config'] or k.startswith(('image.', 'volatile.')) for k in obj['config']), 'Unexpected box configuration')
    target = Path(c['boxes_root']) / name; p.no_symlinks(target)
    expected = {'owner': p.OWNER, 'name': name, 'machine_id': c['machine_id'], 'hdd_uuid': c['hdd_uuid']}
    p.require(json.loads((target / '.chart-incus-box.json').read_text()) == expected, 'Retained HDD marker differs')
    return obj


def host(c):
    p.check_host(c)
    p.require(json.loads(p.HOST_CONFIG.read_text()) == c, 'Installed host config differs')
    for resource, key in [('projects', 'project'), ('networks', 'bridge'), ('storage-pools', 'pool')]:
        obj = p.query('/1.0/' + resource + '/' + c[key])
        p.require(obj['config'].get('user.chart-infra') == p.OWNER, 'Foreign Incus resource')


def candidate(c, fingerprint, verified=False):
    obj = p.query('/1.0/images/' + fingerprint + '?project=' + c['project'])
    p.require(not obj['public'] and obj['properties'].get('chart.owner') == OWNER, 'Expected private bare image')
    if verified:
        record = json.loads((p.STATE / 'bare-images' / obj['properties']['chart.build'] / 'build.json').read_text())
        p.require(record.get('verified') and record['fingerprint'] == fingerprint, 'Bare image has not passed acceptance')
    return obj
