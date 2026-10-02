#!/usr/bin/env python3
"""Acceptance of a fresh seeded box using its own retained synthetic dataset."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '/opt/chart-infra/incus')
import backing as b
import box
import app_checks
import image_guest


def command(script, action, name, *extra):
    return b.run(['python3', '/opt/chart-infra/incus/' + script, action, '--box', name, *extra, '--apply'], timeout=1200)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--box', required=True)
    a = p.parse_args()
    b.require(a.box.startswith('chart-accept-'), 'This fixture operates only on image acceptance boxes')
    marker = b.guard(a.box)
    ip = b.run(['tailscale', 'ip', '-4']).strip()
    b.require(not (b.ROOT / 'identity/npmrc').exists(), 'Acceptance must not have a registry credential')
    report_path = b.ROOT / 'identity/image-accept.json'
    if report_path.exists():
        report = b.read(report_path)
        b.require(report['passed'] and report['seed_sha256'] == b.digest(image_guest.SEED / 'manifest.json'), 'Existing acceptance differs')
        if not any(c['State']['Running'] for c in box.containers()):
            command('backing.py', 'up', a.box)
        command('box.py', 'up', a.box)
        print('Completed fixture retained; services ready for host exposure checks.')
        return
    if not b.STATE.exists():
        command('backing.py', 'prepare', a.box, '--dataset', 'image-fixture', '--tailscale-ip', ip)
    inv = b.inventory(a.box, marker)
    b.require(inv['dataset'] == 'image-fixture', 'Refusing a non-fixture dataset')
    command('backing.py', 'up', a.box)
    if not (box.STATE / 'image-seed.json').exists():
        b.run(['python3', '/opt/chart-infra/incus/image_guest.py', 'adopt', '--box', a.box])
    command('box.py', 'identity', a.box)
    if not (box.STATE / 'selection.json').exists():
        command('box.py', 'select', a.box, '--workspace', 'baseline')
    command('box.py', 'up', a.box)
    # These checks create only this acceptance box's synthetic roles/user/workspace.
    b.run(['python3', '/opt/chart-infra/incus/backing_checks.py', '--box', a.box, '--apply'], timeout=240)
    b.run(['python3', '/opt/chart-infra/incus/app_checks.py', '--box', a.box, '--apply'], timeout=240)
    identity_paths = [b.STATE / 'credentials.json', b.STATE / 'keyfile', box.STATE / 'identity.json', box.STATE / 'api-fixture.json']
    before = {str(path): b.digest(path) for path in identity_paths}
    command('box.py', 'down', a.box)
    command('backing.py', 'down', a.box)
    command('backing.py', 'up', a.box)
    command('box.py', 'up', a.box)
    b.require(before == {str(path): b.digest(path) for path in identity_paths}, 'Retained identities/fixture receipts differ')
    b.run(['python3', '/opt/chart-infra/incus/backing_checks.py', '--box', a.box, '--expect-existing', '--apply'], timeout=240)
    # Respect auth's login-code cooldown; never reset limiter data to accelerate acceptance.
    for _ in range(13): time.sleep(5)
    b.run(['python3', '/opt/chart-infra/incus/app_checks.py', '--box', a.box, '--expect-existing', '--apply'], timeout=240)
    ts = json.loads(b.run(['tailscale', 'status', '--json']))['Self']
    report = {'passed': True, 'box': a.box, 'seed_sha256': b.digest(image_guest.SEED / 'manifest.json'),
              'machine_id': b.digest(Path('/etc/machine-id')),
              'ssh_key': b.digest(b.ROOT / 'identity/ssh/ssh_host_ed25519_key.pub'),
              'tailscale_id': ts['ID'], 'tailscale_ip': ip,
              'mongo_identity': before[str(b.STATE / 'keyfile')],
              'database_credentials': before[str(b.STATE / 'credentials.json')],
              'app_identity': before[str(box.STATE / 'identity.json')],
              'checks': ['loaded_images_and_dependencies_without_registry_credentials', 'replica_transactions_change_streams',
                         'per_service_database_denials', 'runtime_egress', 'api_login_workspace', 'compose_retention',
                         'no_cpu_memory_limits', 'on_demand_restart_policy']}
    for service in box.SERVICES:
        report['signing_' + service] = b.digest(box.STATE / service / 'private.pem')
    report['signing_backend'] = b.digest(box.STATE / 'auth/backend-private.pem')
    b.save(report_path, report)
    print('Guest acceptance passed; services remain ready for host exposure checks.')


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
