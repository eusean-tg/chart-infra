#!/usr/bin/env python3
"""Manage recorded laptop-to-personal-box Mutagen sessions for arbitrary repositories."""
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
import policy


def require(condition, message):
    if not condition: raise RuntimeError(message)


def save(path, data):
    temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(8))
    with temporary.open('x') as f:
        os.fchmod(f.fileno(), 0o600); json.dump(data, f, indent=2); f.write('\n')
        f.flush(); os.fsync(f.fileno())
    temporary.replace(path)


def run(a, *args):
    return subprocess.check_output([a.mutagen, *args], text=True, timeout=a.timeout)


def sessions(a):
    return json.loads(run(a, 'sync', 'list', '--template', '{{json .}}')) or []


def overlap(a, b):
    a, b = Path(a), Path(b)
    return a == b or a in b.parents or b in a.parents


def remote(a, cfg, repo, action, tracked):
    # The source policy travels as code over authenticated SSH, without installing a box CLI.
    code = Path(__file__).with_name('policy.py').read_text() + '''
import socket,sys
q=json.load(sys.stdin)
if socket.gethostname()!=q['box']: raise RuntimeError('Wrong SSH box hostname')
p=Path(q['path'])
if any(x.is_symlink() for x in (p,*p.parents)): raise RuntimeError('Symlinked destination')
if q['action']=='prepare': p.mkdir(parents=True,exist_ok=True)
files=manifest(p,q['tracked'])
print(json.dumps({'fingerprint':fingerprint(files),'files':len(files)}))
'''
    args = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', cfg['ssh'], shlex.join(['python3', '-c', code])]
    query = {'box': cfg['box'], 'path': cfg['source_root'] + '/' + repo, 'action': action, 'tracked': tracked}
    return json.loads(subprocess.check_output(args, input=json.dumps(query), text=True, timeout=a.timeout))


def validate(cfg, repo, rec, session):
    require(session.get('name') == rec['name'] and session.get('labels', {}).get('chart-owner') == rec['owner'], 'Session ownership differs')
    alpha, beta = session['alpha'], session['beta']
    user, host = cfg['ssh'].split('@', 1)
    require(alpha['protocol'] == 'local' and alpha['path'] == rec['source'] and beta['protocol'] == 'ssh'
            and beta.get('user') == user and beta.get('host') == host
            and beta['path'] == cfg['source_root'] + '/' + repo, 'Recorded endpoints differ')
    for key, value in policy.configuration(rec['tracked']).items():
        require(session.get(key) == value, 'Session policy differs: ' + key)
    require(session.get('creatingVersion') == policy.VERSION, 'Session created with a different Mutagen version')
    return session


def owned(a, cfg, repo):
    rec = cfg['records'][repo]
    matches = [r for r in sessions(a) if r['identifier'] == cfg['sessions'].get(repo)]
    require(len(matches) == 1, 'Owned session missing; inspect retained state')
    return validate(cfg, repo, rec, matches[0])


def check_policy(a, cfg, repo):
    rec = cfg['records'][repo]
    tracked = policy.exceptions(rec['source'])
    if tracked != rec['tracked']:
        session = owned(a, cfg, repo)
        if not session.get('paused'): run(a, 'sync', 'pause', session['identifier'])
        raise RuntimeError('Tracked private-pattern paths changed; session paused. Use refresh, then resume and flush.')
    return tracked


def no_overlap(a, cfg, repo, source):
    target = cfg['source_root'] + '/' + repo
    for session in sessions(a):
        if session.get('name') == cfg['records'][repo]['name']:
            validate(cfg, repo, cfg['records'][repo], session); continue
        for side in ('alpha', 'beta'):
            endpoint = session[side]
            if endpoint['protocol'] == 'local': require(not overlap(source, endpoint['path']), 'Source overlaps another Mutagen session')
            elif endpoint['protocol'] == 'ssh' and endpoint.get('user', '') + '@' + endpoint.get('host', '') == cfg['ssh']:
                require(not overlap(target, endpoint['path']), 'Destination overlaps another Mutagen session')
    configs = a.syncthing_config or [str(Path.home() / p) for p in (
        'Library/Application Support/Syncthing/config.xml', '.local/state/syncthing/config.xml', '.config/syncthing/config.xml')]
    for filename in configs:
        path = Path(filename).expanduser()
        if path.exists():
            for folder in ET.parse(path).getroot().findall('folder'):
                if folder.get('path'): require(not overlap(source, Path(folder.get('path')).expanduser().resolve()), 'Source overlaps Syncthing')


def setup(a, cfg, config_path):
    repo = a.repo
    require(a.source and Path(a.source).expanduser().is_absolute(), 'Supply an absolute --source checkout path')
    source = Path(a.source).expanduser().resolve(strict=True)
    root = subprocess.check_output(['git', '-C', str(source), 'rev-parse', '--show-toplevel'], text=True).strip()
    require(str(source) == root, 'Select the Git checkout root')
    tracked = policy.exceptions(source); policy.manifest(source, tracked)
    if repo not in cfg['records']:
        require(not any(overlap(source, path) for path in cfg['repos']), 'Checkout already registered/overlapping')
        cfg['records'][repo] = {'name': 'chart-bare-' + cfg['box'] + '-' + repo, 'owner': secrets.token_hex(16),
                                'source': str(source), 'tracked': tracked, 'phase': 'creating'}
        cfg['repos'][str(source)] = repo
        save(config_path, cfg)
    rec = cfg['records'][repo]
    require(rec['source'] == str(source) and rec['tracked'] == tracked, 'Mapping/policy differs; pause and refresh explicitly')
    no_overlap(a, cfg, repo, str(source))
    existing = [s for s in sessions(a) if s.get('name') == rec['name']]
    if not existing:
        require(not cfg['sessions'].get(repo), 'Recorded session disappeared; do not replace implicitly')
        beta = remote(a, cfg, repo, 'prepare', tracked)
        require(beta['files'] == 0 or rec.get('refreshing'), 'Destination contains source; preserve it and choose a fresh repo destination')
        filename = config_path.parent / (repo + '-mutagen.json')
        save(filename, {'sync': {'defaults': policy.configuration(tracked)}})
        run(a, 'sync', 'create', '--paused', '--no-global-configuration', '-c', str(filename),
            '--name', rec['name'], '--label', 'chart-owner=' + rec['owner'], str(source), cfg['ssh'] + ':' + cfg['source_root'] + '/' + repo)
        existing = [s for s in sessions(a) if s.get('name') == rec['name']]
    require(len(existing) == 1, 'Cannot identify created session')
    session = validate(cfg, repo, rec, existing[0])
    require(not cfg['sessions'].get(repo) or cfg['sessions'][repo] == session['identifier'], 'Session ID replaced')
    cfg['sessions'][repo] = session['identifier']; rec['phase'] = 'ready'; rec.pop('refreshing', None)
    save(config_path, cfg)
    print('Recorded', repo, session['identifier'], '; new sessions remain paused.')


def refresh(a, cfg, path):
    repo = a.repo; rec = cfg['records'][repo]
    tracked = policy.exceptions(rec['source'])
    if rec['phase'] != 'refreshing':
        session = owned(a, cfg, repo)
        require(session.get('paused'), 'Pause the session before changing its tracking policy')
        if tracked == rec['tracked']: print('Policy unchanged.'); return
        rec.setdefault('history', []).append({'session': session['identifier'], 'tracked': rec['tracked']})
        rec.update(phase='refreshing', pending_tracked=tracked)
        save(path, cfg)
    require(rec['pending_tracked'] == tracked, 'Tracking changed during refresh; retain state and inspect')
    matches = [s for s in sessions(a) if s.get('name') == rec['name']]
    if matches:
        require(len(matches) == 1, 'Ambiguous session during refresh')
        session = validate(cfg, repo, rec, matches[0])
        require(session['identifier'] == cfg['sessions'][repo] and session.get('paused'), 'Session changed during refresh')
        # Termination removes only this session record; both endpoint trees remain.
        run(a, 'sync', 'terminate', session['identifier'])
    cfg['sessions'].pop(repo); rec.update(tracked=tracked, phase='creating', refreshing=True)
    rec.pop('pending_tracked', None); save(path, cfg)
    a.source = rec['source']; setup(a, cfg, path)


def flush(a, cfg, repo):
    rec = cfg['records'][repo]; tracked = check_policy(a, cfg, repo)
    session = owned(a, cfg, repo)
    require(not session.get('paused'), 'Session is paused; resume it before flush')
    before = policy.fingerprint(policy.manifest(rec['source'], tracked))
    run(a, 'sync', 'flush', session['identifier'])
    session = owned(a, cfg, repo)
    require(not session.get('paused') and not any(session.get(k) for k in ('conflicts', 'excludedConflicts', 'lastError')), 'Synchronization has conflicts/errors')
    for side in ('alpha', 'beta'):
        endpoint = session[side]
        require(endpoint.get('connected') and not any(endpoint.get(k) for k in ('scanProblems', 'transitionProblems', 'excludedScanProblems', 'excludedTransitionProblems')), 'Synchronization endpoint has errors/is disconnected')
    result = remote(a, cfg, repo, 'verify', tracked)
    check_policy(a, cfg, repo)
    require(before == policy.fingerprint(policy.manifest(rec['source'], tracked)) == result['fingerprint'], 'Source changed or remote contents differ; stop edits and inspect')
    print(json.dumps({'box': cfg['box'], 'repo': repo, **result}, indent=2))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['setup', 'status', 'pause', 'resume', 'flush', 'refresh'])
    parser.add_argument('--config', default=str(Path.home() / '.config/chart-box/box.json'))
    parser.add_argument('--box'); parser.add_argument('--ssh'); parser.add_argument('--repo', required=True)
    parser.add_argument('--source'); parser.add_argument('--mutagen', default='mutagen')
    parser.add_argument('--timeout', type=int, default=180); parser.add_argument('--syncthing-config', action='append')
    a = parser.parse_args()
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,60}', a.repo) and a.repo not in ('.', '..'), 'Use a simple repo directory name')
    path = Path(a.config).expanduser().absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'Config path must not contain symlinks')
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists(): cfg = json.loads(path.read_text())
        else:
            require(a.action == 'setup' and a.box, 'Run setup with --box first')
            cfg = {'schema': 1, 'box': a.box, 'ssh': a.ssh or 'root@' + a.box, 'source_root': '/srv/chart/source', 'repos': {}, 'sessions': {}, 'records': {}}
        require(re.fullmatch('[a-z][a-z0-9-]{1,40}', cfg['box']), 'Invalid box name')
        require(re.fullmatch(r'root@[A-Za-z0-9][A-Za-z0-9.-]*', cfg['ssh']), 'Use root@host or root@SSH-alias')
        require(cfg['schema'] == 1 and cfg['source_root'] == '/srv/chart/source', 'Unexpected config layout')
        require(not a.box or cfg['box'] == a.box, 'Different box selected in config')
        require(not a.ssh or cfg['ssh'] == a.ssh, 'Different SSH target selected')
        require(run(a, 'version').strip() == policy.VERSION, 'Use Mutagen ' + policy.VERSION)
        if a.action == 'setup': setup(a, cfg, path)
        else:
            require(a.repo in cfg['records'], 'Repository is not registered')
            session = owned(a, cfg, a.repo)
            if a.action == 'status': print(json.dumps({'box': cfg['box'], 'session': session, 'policy_refresh_needed': policy.exceptions(cfg['records'][a.repo]['source']) != cfg['records'][a.repo]['tracked']}, indent=2))
            elif a.action == 'refresh': refresh(a, cfg, path)
            elif a.action == 'flush': flush(a, cfg, a.repo)
            else:
                if a.action == 'resume': check_policy(a, cfg, a.repo)
                print(run(a, 'sync', a.action, session['identifier']))


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
