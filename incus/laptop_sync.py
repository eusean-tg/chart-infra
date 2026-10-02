#!/usr/bin/env python3
"""Own one laptop-to-box Mutagen session without touching k3s session records."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import sync_policy as policy
from sync_policy import common


def remote(a, action, payload=None):
    command = shlex.join(['python3', a.remote_dir + '/box_sync.py', action, '--box', a.box,
                         '--workspace', a.workspace, '--service', a.service])
    return json.loads(subprocess.check_output(['ssh', '-o', 'BatchMode=yes', a.host, command],
                      input=None if payload is None else json.dumps(payload), text=True, timeout=a.timeout))


def session_name(a):
    return f'chart-box-{a.box}-{a.workspace}-{a.service}'


def validate_session(a, r, state):
    user, host = a.host.split('@', 1)
    if r.get('name') != session_name(a) or r.get('labels', {}).get('chart-owner') != state['client_id']:
        raise RuntimeError('Mutagen session ownership differs')
    alpha, beta = r['alpha'], r['beta']
    if (alpha['protocol'] != 'local' or alpha['path'] != state['path'] or beta['protocol'] != 'ssh'
            or beta.get('user') != user or beta.get('host') != host or beta['path'] != state['remote']['path']):
        raise RuntimeError('Mutagen endpoints differ from recorded mapping')
    for k, value in policy.configuration().items():
        if r.get(k) != value:
            raise RuntimeError('Mutagen policy differs: ' + k)
    if beta.get('stageMode') != 'neighboring' or r.get('creatingVersion') != policy.VERSION:
        raise RuntimeError('Mutagen version/staging differs')
    return r


def owned(a, state):
    matches = [r for r in common.sessions(a.mutagen) if r['identifier'] == state.get('session')]
    if len(matches) != 1:
        raise RuntimeError('Owned box session missing; inspect instead of recreating')
    return validate_session(a, matches[0], state)


def overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def plan(a, saved):
    exp = remote(a, 'export')  # SSH preflight precedes querying/starting a local daemon.
    if exp['policy_hash'] != policy.policy_hash() or exp['version'] != policy.VERSION or exp['owner'] != 'root':
        raise RuntimeError('Box and laptop helper policies differ')
    if common.mutagen(a.mutagen, 'version').strip() != policy.VERSION:
        raise RuntimeError('Use Mutagen ' + policy.VERSION)
    raw = a.source or (saved or {}).get('path')
    if not raw or not Path(raw).expanduser().is_absolute():
        raise RuntimeError('Supply --source with the explicit absolute laptop checkout path')
    existing = []
    candidates = [Path(p).expanduser() for p in a.syncthing_config] if a.syncthing_config else [
        Path.home() / 'Library/Application Support/Syncthing/config.xml',
        Path.home() / '.local/state/syncthing/config.xml', Path.home() / '.config/syncthing/config.xml']
    for p in candidates:
        if p.exists():
            existing.extend(('Syncthing ' + f.get('id', 'folder'), f.get('path'))
                            for f in ET.parse(p).getroot().findall('folder') if f.get('path'))
    records = common.sessions(a.mutagen)
    for r in records:
        if saved and r.get('labels', {}).get('chart-owner') == saved['client_id']:
            validate_session(a, r, saved)
            continue
        if r.get('name') == session_name(a):
            raise RuntimeError('Unowned session name collision')
        for side in ('alpha', 'beta'):
            e = r[side]
            if e['protocol'] == 'local':
                existing.append(('Mutagen ' + r['identifier'], e['path']))
            elif e['protocol'] == 'ssh' and e.get('user', '') + '@' + e.get('host', '') == a.host:
                if overlap(Path(exp['path']), Path(e['path'])):
                    raise RuntimeError('Box destination overlaps another session')
    path = common.check_roots({a.service: raw}, existing)[a.service]
    policy.manifest(path)
    expected = {'host': a.host, 'remote_dir': a.remote_dir, 'remote': exp, 'path': path}
    if saved and any(saved.get(k) != v for k, v in expected.items()):
        raise RuntimeError('Saved box mapping differs; no implicit reassignment')
    return saved or expected


def payload(state):
    return {'registration': state['remote']['registration'], 'policy_hash': policy.policy_hash(),
            'client_id': state['client_id'], 'session': state['session']}


def setup(a, saved, path):
    state = plan(a, saved)
    if not saved:
        state.update(client_id=secrets.token_hex(24), phase='creating-paused')
        common.write_json(path, state)
    config = path.parent / 'mutagen-sync.yml'
    if config.exists() and config.read_text() != policy.config_text():
        raise RuntimeError('Local Mutagen config changed; inspect before setup')
    config.write_text(policy.config_text())
    found = [r for r in common.sessions(a.mutagen) if r.get('name') == session_name(a)]
    if not found:
        if state.get('session'):
            raise RuntimeError('Recorded session disappeared; no automatic recreation')
        common.mutagen(a.mutagen, 'sync', 'create', '--paused', '--no-global-configuration',
                       '-c', str(config), '--stage-mode-beta', 'neighboring', '--name', session_name(a),
                       '--label', 'chart-owner=' + state['client_id'], state['path'],
                       a.host + ':' + state['remote']['path'], timeout=a.timeout)
        found = [r for r in common.sessions(a.mutagen) if r.get('name') == session_name(a)]
    if len(found) != 1:
        raise RuntimeError('Cannot identify owned session')
    r = validate_session(a, found[0], state)
    if state.get('session') and state['session'] != r['identifier']:
        raise RuntimeError('Recorded session identity replaced')
    state['session'] = r['identifier']
    common.write_json(path, state)
    remote(a, 'register', {**payload(state), 'path': state['path']})
    state['phase'] = 'registered'
    common.write_json(path, state)
    print('Owned box session registered; new sessions are paused. Existing sessions are unchanged.')


def clean(r, paused=False):
    if bool(r.get('paused', False)) != paused or r.get('conflicts') or r.get('excludedConflicts') or r.get('lastError'):
        return False
    for side in ('alpha', 'beta'):
        e = r[side]
        if any(e.get(k) for k in ('scanProblems', 'transitionProblems', 'excludedScanProblems', 'excludedTransitionProblems')):
            return False
        if not paused and not e.get('connected'):
            return False
    return paused or r.get('status') == 'watching'


def snapshot(state):
    path = state['path']
    return {'fingerprint': policy.fingerprint(policy.manifest(path)),
            'head': subprocess.check_output(['git', '-C', path, 'rev-parse', 'HEAD'], text=True).strip(),
            'dirty': bool(subprocess.check_output(['git', '-C', path, 'status', '--porcelain'], text=True))}


def change(a, state, action):
    owned(a, state)
    remote(a, 'invalidate', payload(state))
    print(common.mutagen(a.mutagen, 'sync', action, state['session'], timeout=a.timeout).strip())


def checkpoint(a, state, freeze):
    if owned(a, state).get('paused'):
        raise RuntimeError('Resume the owned session before wait/freeze')
    before = snapshot(state)
    common.mutagen(a.mutagen, 'sync', 'flush', state['session'], timeout=a.timeout)
    deadline = time.monotonic() + a.timeout
    while time.monotonic() < deadline:
        r = owned(a, state)
        if clean(r):
            break
        if r.get('conflicts') or r.get('excludedConflicts') or r.get('lastError'):
            raise RuntimeError('Sync conflicts/errors; preserve both sides and inspect')
        time.sleep(0.5)
    else:
        raise RuntimeError('Sync did not reach clean watching state')
    if freeze:
        remote(a, 'invalidate', payload(state))
        common.mutagen(a.mutagen, 'sync', 'pause', state['session'], timeout=a.timeout)
        if not clean(owned(a, state), paused=True):
            raise RuntimeError('Owned session not cleanly paused')
    after = snapshot(state)
    if before != after:
        raise RuntimeError('Source changed during verification; stop edits, resume if paused, retry')
    result = remote(a, 'checkpoint', {**payload(state), **after, 'paused': freeze})
    print(json.dumps(result, indent=2))


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['plan', 'setup', 'status', 'resume', 'pause', 'wait', 'freeze'])
    p.add_argument('--host', required=True, help='root@host or root@SSH-alias')
    p.add_argument('--box', required=True)
    p.add_argument('--workspace', required=True)
    p.add_argument('--service', required=True, choices=list(policy.REPOS))
    p.add_argument('--remote-dir', default='/opt/chart-infra/incus')
    p.add_argument('--source', help='Explicit absolute laptop checkout path; remembered after setup')
    p.add_argument('--mutagen', default='mutagen')
    p.add_argument('--timeout', type=int, default=180)
    p.add_argument('--syncthing-config', action='append')
    a = p.parse_args()
    for value in (a.box, a.workspace):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,40}', value):
            p.error('Invalid box/workspace name')
    if not re.fullmatch(r'root@[A-Za-z0-9][A-Za-z0-9.-]*', a.host):
        p.error('Use root@host or root@SSH-alias')
    if not Path(a.remote_dir).is_absolute():
        p.error('Remote helper directory must be absolute')
    root = Path.home() / '.local/state/chart-infra/mutagen-box' / a.box / a.workspace / a.service
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (root / 'session.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = root / 'session.json'
        saved = json.loads(path.read_text()) if path.exists() else None
        if a.action == 'setup':
            setup(a, saved, path)
            return
        state = plan(a, saved)
        if a.action == 'plan':
            print(json.dumps(state, indent=2))
            return
        if not saved or saved.get('phase') != 'registered':
            raise RuntimeError('Complete plan/setup first')
        if a.action == 'status':
            print(json.dumps({'session': owned(a, state), 'box': remote(a, 'status')}, indent=2))
        elif a.action in ('resume', 'pause'):
            change(a, state, a.action)
        else:
            checkpoint(a, state, a.action == 'freeze')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
