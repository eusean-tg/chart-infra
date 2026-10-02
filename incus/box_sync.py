#!/usr/bin/env python3
"""Retain bundle sources and attest laptop-owned mirrors inside an Incus box."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import sys

import backing as b
import box
import sync_policy as policy


def state_path(w, service):
    box.name(w)
    b.require(service in box.SERVICES, 'Unknown service')
    return b.safe(box.STATE / 'sync' / w / (service + '.json'))


def read(w, service):
    r = b.read(state_path(w, service))
    b.require(r['policy_hash'] == policy.policy_hash() and r['workspace'] == w
              and r['service'] == service, 'Mirror policy/identity differs')
    return r


def save(w, service, r):
    p = state_path(w, service)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    b.save(p, r)


def tree_id(path):
    st = b.safe(path).stat()
    return [st.st_dev, st.st_ino]


def writers(w):
    running = [c for c in box.containers() if c['State']['Running']]
    root = box.SOURCE / w
    return [c for c in running if not c.get('Mounts') or any(
        Path(m.get('Source', '/')) == root or root in Path(m.get('Source', '/')).parents
        for m in c['Mounts'])]


def stopped(w):
    b.require(not writers(w),
              'Stop app writers before source handover or dependency preparation')


def prepare(w, service):
    stopped(w)
    box.capacity()
    p = state_path(w, service)
    target = b.safe(box.SOURCE / w / box.SERVICES[service][0])
    if p.exists():
        r = read(w, service)
        if r['phase'] == 'ready':
            validate_source(w, service, box.workspace(w)[service], frozen=False, allow_pending=True)
            return export(w, service)
    else:
        previous = box.source_record(w, service)
        b.require(previous['kind'] == 'bundle', 'Handover requires a registered immutable bundle')
        token = secrets.token_hex(24)
        retained = b.safe(box.SOURCE / '.retained' / w / service / token)
        b.require(not retained.exists(), 'Retained source destination exists')
        r = {'phase': 'intent', 'policy_hash': policy.policy_hash(), 'workspace': w,
             'service': service, 'registration': token, 'previous': previous,
             'original_inode': tree_id(target), 'retained': str(retained)}
        save(w, service, r)
    b.require(r['phase'] == 'intent', 'Unknown handover phase; inspect retained journal')
    retained = b.safe(Path(r['retained']))
    if not retained.exists():
        b.require(tree_id(target) == r['original_inode'], 'Source directory replaced during handover')
        b.require(policy.fingerprint(policy.manifest(target)) == r['previous']['fingerprint'],
                  'Seed content changed during handover; preserve and inspect it')
        retained.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.rename(retained)
    b.require(tree_id(retained) == r['original_inode'] and
              policy.fingerprint(policy.manifest(retained)) == r['previous']['fingerprint'],
              'Retained seed differs; refusing recovery')
    if target.exists():
        b.require(target.is_dir() and not policy.manifest(target) and
                  all(p.name == 'node_modules' and p.is_dir() and not p.is_symlink() and not any(p.iterdir())
                      for p in target.iterdir()), 'Unexpected content in interrupted mirror preparation')
    else:
        target.mkdir(mode=0o755)
    target.chmod(0o755)
    (target / 'node_modules').mkdir(mode=0o755, exist_ok=True)
    (target / 'node_modules').chmod(0o755)
    r['mirror_inode'] = tree_id(target)
    records = box.workspace(w)
    records[service] = {'kind': 'mirror', 'path': str(target), 'registration': r['registration']}
    b.save(box.STATE / 'sources' / (w + '.json'), records)
    r['phase'] = 'ready'
    save(w, service, r)
    return export(w, service)


def export(w, service):
    r = read(w, service)
    b.require(r['phase'] == 'ready', 'Interrupted handover; rerun prepare while apps are stopped')
    return {'transport': 'mutagen-ssh', 'policy': policy.POLICY, 'policy_hash': policy.policy_hash(),
            'version': policy.VERSION, 'workspace': w, 'service': service, 'owner': 'root',
            'registration': r['registration'], 'path': str(box.SOURCE / w / box.SERVICES[service][0])}


def validate_source(w, service, record, frozen=False, allow_pending=False):
    r = read(w, service)
    b.require(r['phase'] == 'ready' and record.get('registration') == r['registration'],
              'Incomplete source handover; inspect sync journal')
    path = b.safe(Path(record['path']))
    b.require(tree_id(path) == r['mirror_inode'], 'Mirror directory replaced; explicit recovery required')
    if allow_pending:
        return record
    checkpoint = r.get('checkpoint')
    b.require(checkpoint, 'Mirror has no verified checkpoint; run laptop wait/freeze')
    if frozen:
        b.require(checkpoint['paused'], 'Freeze the owned laptop session before this operation')
        b.require(policy.fingerprint(policy.manifest(path)) == checkpoint['fingerprint'],
                  'Mirror changed after freeze; resume and freeze again')
    return {**record, 'fingerprint': checkpoint['fingerprint']}


def validate_payload(r, payload, registered=True):
    b.require(payload.get('registration') == r['registration'] and
              payload.get('policy_hash') == policy.policy_hash(), 'Mirror registration/policy differs')
    b.require(re.fullmatch(r'[a-f0-9]{48}', payload.get('client_id', '')), 'Invalid client identity')
    if registered:
        b.require(r.get('client_id') == payload['client_id'] and r.get('session') == payload.get('session'),
                  'Laptop/session ownership differs')


def register(w, service, payload):
    r = read(w, service)
    export(w, service)
    validate_payload(r, payload, registered=False)
    b.require(re.fullmatch(r'sync_[A-Za-z0-9]+', payload.get('session', '')), 'Invalid Mutagen session ID')
    b.require(Path(payload.get('path', '')).is_absolute(), 'Explicit absolute laptop path required')
    fields = {k: payload[k] for k in ('client_id', 'session', 'path')}
    if 'client_id' in r:
        b.require(all(r.get(k) == v for k, v in fields.items()), 'Existing laptop registration differs')
    else:
        stopped(w)
        b.require(not policy.manifest(box.SOURCE / w / box.SERVICES[service][0]),
                  'Initial mirror contains files; inspect before registration')
        r.update(fields)
        save(w, service, r)
    return {'registered': True}


def checkpoint(w, service, payload):
    r = read(w, service)
    validate_payload(r, payload)
    validate_source(w, service, box.workspace(w)[service], allow_pending=True)
    b.require(type(payload.get('paused')) is bool, 'Explicit pause state required')
    b.require(re.fullmatch(r'[a-f0-9]{40,64}', payload.get('head', '')) and
              type(payload.get('dirty')) is bool, 'Invalid laptop source observation')
    path = box.SOURCE / w / box.SERVICES[service][0]
    actual = policy.fingerprint(policy.manifest(path))
    b.require(actual == payload.get('fingerprint'), 'Laptop/box fingerprints differ; preserve both sides and inspect')
    b.require((path / 'package.json').is_file() and (path / 'pnpm-lock.yaml').is_file()
              and (path / 'src/index.ts').is_file(), 'Incomplete backend source')
    r['checkpoint'] = {k: payload[k] for k in ('paused', 'fingerprint', 'head', 'dirty')}
    save(w, service, r)
    return r['checkpoint']


def invalidate(w, service, payload):
    r = read(w, service)
    validate_payload(r, payload)
    validate_source(w, service, box.workspace(w)[service], allow_pending=True)
    if writers(w):
        inv = box.selection()
        b.require(inv['workspace'] == w and inv.get('sources', {}).get(service) == r['registration'],
                  'Running apps have not selected this mirror; stop them before transfer')
    if r.get('checkpoint'):
        r['checkpoint']['paused'] = False
    save(w, service, r)
    return {'frozen': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'export', 'register', 'checkpoint', 'invalidate', 'status'])
    p.add_argument('--box', required=True)
    p.add_argument('--workspace', required=True)
    p.add_argument('--service', required=True, choices=list(box.SERVICES))
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    os.umask(0o077)
    marker = b.guard(a.box)
    with open('/run/lock/chart-backing.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        b.inventory(a.box, marker)
        b.ownership(a.box)
        box.registry(a.box)
        if a.action == 'prepare':
            if not a.apply:
                print(json.dumps({'plan': 'retain registered bundle and prepare empty mirror',
                                  'workspace': a.workspace, 'service': a.service}))
                return
            result = prepare(a.workspace, a.service)
        elif a.action in ('export', 'status'):
            result = export(a.workspace, a.service) if a.action == 'export' else read(a.workspace, a.service)
        else:
            payload = json.load(sys.stdin)
            result = globals()[a.action](a.workspace, a.service, payload)
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
