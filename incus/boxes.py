#!/usr/bin/env python3
"""Create personal boxes from verified bare images; recreate with retained HDD identity."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import prep as p
import host_common as h
import image
import backup


def scripts(c, fingerprint):
    candidate = h.candidate(c, fingerprint, verified=True)
    root = p.STATE / 'bare-images' / candidate['properties']['chart.build'] / 'scripts'
    p.require(root.is_dir(), 'Recorded identity provisioning scripts missing')
    return root


def recreate(c, name, fingerprint, script_dir):
    obj = h.owned(c, name)
    p.require(obj['status'] == 'Running', 'Start the selected box and verify it before planned recreation')
    stamp = str(time.time_ns())
    private = p.STATE / 'recreations' / (name + '-' + stamp)
    private.mkdir(parents=True, mode=0o700)
    retired = 'retained-' + stamp
    keys = h.guest(c, name, ['cat', '/root/.ssh/authorized_keys'])
    p.require(keys.strip(), 'Authorized key set missing')
    p.write_owned(private / 'authorized_keys', keys)
    paths = ['/srv/chart/data/identity/ssh/ssh_host_ed25519_key', '/srv/chart/data/identity/ssh/ssh_host_rsa_key']
    # Tailscale rewrites its state after startup; compare its logical node identity separately.
    identity = {'ssh': h.guest(c, name, ['sha256sum', *paths]),
                'tailscale_id': json.loads(h.guest(c, name, ['tailscale', 'status', '--json']))['Self']['ID']}
    record = {'box': name, 'retained_instance': retired, 'fingerprint': fingerprint, 'identity': identity, 'phase': 'backup'}
    h.save(private / 'recreate.json', record)
    export = backup.backup(c, name, rootfs=True, resume=False)
    record.update(backup=str(export), phase='backed-up'); h.save(private / 'recreate.json', record)
    # Validate the independent data archive before releasing the original attachment.
    scratch = backup.scratch_root() / (name + '-' + stamp)
    record['restore_check'] = backup.restore(export, scratch, c)
    h.save(private / 'recreate.json', record)
    p.require(h.instance(c, name)['status'] == 'Stopped', 'Original box unexpectedly running')
    p.run(['incus', 'config', 'device', 'remove', 'local:' + name, 'data', '--project', c['project']])
    p.run(['incus', 'move', 'local:' + name, 'local:' + retired, '--project', c['project']])
    record['phase'] = 'original-retained'; h.save(private / 'recreate.json', record)
    image.create(c, name, fingerprint, private / 'authorized_keys', script_dir, adopt=True)
    actual = h.guest(c, name, ['sha256sum', *paths])
    p.require(actual == identity['ssh'], 'SSH identity differs after adoption')
    deadline = time.monotonic() + 45
    while True:
        ts = json.loads(h.guest(c, name, ['tailscale', 'status', '--json']))
        if ts.get('BackendState') == 'Running': break
        p.require(time.monotonic() < deadline, 'Retained Tailscale identity did not become ready; inspect the replacement')
        time.sleep(1)
    p.require(ts['Self']['ID'] == identity['tailscale_id'], 'Tailscale identity differs after adoption')
    record['phase'] = 'recreated'; h.save(private / 'recreate.json', record)
    print(json.dumps({'box': name, 'retained_rootfs': retired, 'backup': str(export),
                      'note': 'Reinstall projects/dependencies; reconcile paused laptop sessions explicitly.'}, indent=2))
    return record


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['create', 'recreate'])
    parser.add_argument('--config', required=True); parser.add_argument('--box', required=True)
    parser.add_argument('--image', required=True, help='Verified immutable bare-image fingerprint')
    parser.add_argument('--ssh-key'); parser.add_argument('--sync-paused', action='store_true')
    parser.add_argument('--apply', action='store_true')
    a = parser.parse_args(); c = p.config(a.config); p.check_host(c); p.box_name(a.box)
    p.require(len(a.image) == 64 and all(x in '0123456789abcdef' for x in a.image), 'Supply a full fingerprint')
    key = None
    if a.command == 'create':
        p.require(a.ssh_key, 'Supply the developer public key'); key = p.public_key(a.ssh_key)
    else: p.require(a.sync_paused, 'Coordinate and pause laptop sessions before recreation; acknowledge with --sync-paused')
    if not a.apply:
        print('Plan:', a.command, a.box, 'from', a.image, '; preserve HDD identity; no application setup.'); return
    with p.locked():
        h.host(c); script_dir = scripts(c, a.image)
        if a.command == 'create':
            private = p.STATE / 'bare-boxes' / a.box
            p.require(not private.exists(), 'Retained creation record exists; inspect partial state')
            p.write_owned(private / 'authorized_key', key)
            h.save(private / 'create.json', {'box': a.box, 'fingerprint': a.image})
            image.create(c, a.box, a.image, private / 'authorized_key', script_dir)
            print('Enroll with: sudo incus exec local:' + a.box + ' --project ' + c['project'] + ' -- tailscale up --hostname=' + a.box + ' --accept-routes=false --accept-dns=true --ssh=false')
        else: recreate(c, a.box, a.image, script_dir)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
