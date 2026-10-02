#!/usr/bin/env python3
"""Prepare and operate chart application containers inside an enrolled Incus box."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

import backing as b
import box_config
try:
    import sync_common as source_policy
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import sync_common as source_policy

STATE = b.ROOT / 'identity/apps'
SOURCE = Path('/srv/chart/source')
OWNER = 'chart-incus-apps-v1'
PROJECT = 'chart-apps'
SERVICES = box_config.SERVICES
INPUTS = ('package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', '.pnpmfile.cjs')


def capacity():
    usage = shutil.disk_usage('/')
    fraction = usage.used / usage.total
    b.require(fraction < 0.85, 'SSD pool at 85% usage; inspect capacity before preparing more source/dependencies')
    if fraction >= 0.70:
        print('SSD pool at the 70% review threshold; inspect host reserve and btrfs allocation before further growth.', file=sys.stderr)


def name(value):
    b.require(bool(re.fullmatch(r'[a-z][a-z0-9-]{0,31}', value or '')), 'Use a simple lowercase name')
    return value


def registry(box, create=False):
    path = b.safe(STATE / 'registry.json')
    if not path.exists():
        b.require(create and not STATE.exists(), 'App registration missing; run runtime preparation or inspect partial state')
        STATE.mkdir(mode=0o700)
        b.save(path, {'owner': OWNER, 'box': box})
    reg = b.read(path)
    b.require(reg == {'owner': OWNER, 'box': box}, 'App registration differs')


def runtime():
    r = b.read(STATE / 'runtime.json')
    b.require(re.fullmatch(r'sha256:[a-f0-9]{64}', r['image']), 'Invalid runtime image')
    obj = json.loads(b.run(['docker', 'image', 'inspect', r['image']]))[0]
    b.require(obj['Id'] == r['image'] and obj['Config']['User'] == '1000:1000', 'Runtime image/UID differs')
    return r


def register_runtime(image):
    b.require(re.fullmatch(r'sha256:[a-f0-9]{64}', image or ''), 'Supply the loaded immutable --image ID')
    if (STATE / 'runtime.json').exists():
        b.require(runtime()['image'] == image, 'Explicit runtime migration required')
        print('Existing toolchain image retained.')
        return
    obj = json.loads(b.run(['docker', 'image', 'inspect', image]))[0]
    b.require(obj['Id'] == image and obj['Config']['User'] == '1000:1000', 'Expected pinned UID 1000 toolchain')
    versions = b.run(['docker', 'run', '--rm', '--network=none', '--read-only', '--cap-drop=ALL',
                      '--security-opt=no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777',
                      image, 'sh', '-ec', 'node --version; pnpm --version'])
    b.require(versions.splitlines() == ['v24.20.0', '11.28.2'], 'Toolchain versions differ')
    b.save(STATE / 'runtime.json', {'image': image, 'node': '24.20.0', 'pnpm': '11.28.2'})
    print('Loaded Node/pnpm image registered; no image pull or rebuild.')


def workspace(w):
    name(w)
    p = b.safe(STATE / 'sources' / (w + '.json'))
    return b.read(p) if p.exists() else {}


def source_record(w, service, frozen=False):
    record = workspace(w)[service]
    path = b.safe(SOURCE / w / SERVICES[service][0])
    b.require(record['path'] == str(path), 'Source contract differs')
    if (STATE / 'sync' / w / (service + '.json')).exists():
        import box_sync
        b.require(box_sync.read(w, service)['phase'] == 'ready', 'Incomplete source handover; rerun sync prepare')
    if record['kind'] == 'mirror':
        import box_sync
        return box_sync.validate_source(w, service, record, frozen=frozen)
    b.require(record['kind'] == 'bundle', 'Unknown source contract')
    actual = source_policy.fingerprint(source_policy.manifest(path))
    b.require(actual == record['fingerprint'], 'Baseline source changed; use an explicit workspace, never patch this bundle')
    return record


def stage(w, service, archive, sha, revision):
    name(w)
    b.require(re.fullmatch(r'[a-f0-9]{40}', revision or ''), 'Supply recorded stock commit --revision')
    archive = b.safe(Path(archive))
    b.require(b.digest(archive) == sha, 'Source archive checksum differs')
    records = workspace(w)
    if service in records:
        r = source_record(w, service)
        b.require(r['archive_sha256'] == sha and r['revision'] == revision, 'Existing workspace differs')
        print('Matching source bundle retained.')
        return
    target = b.safe(SOURCE / w / SERVICES[service][0])
    b.require(not target.exists(), 'Unregistered source path exists; inspect interrupted staging')
    capacity()
    with tarfile.open(archive) as tar:
        selected = []
        seen = set()
        for member in tar:
            rel = PurePosixPath(member.name)
            b.require(not rel.is_absolute() and '..' not in rel.parts, 'Unsafe source archive path')
            if source_policy.ignored(rel.parts):
                continue
            b.require(member.isfile() or member.isdir(), 'Included source symlink/device/hardlink refused')
            b.require(rel.as_posix().casefold() not in seen, 'Duplicate/case-colliding source path')
            seen.add(rel.as_posix().casefold())
            selected.append(member)
        target.mkdir(parents=True, mode=0o755)
        target.parent.chmod(0o755)
        for member in selected:
            path = target / member.name
            if member.isdir():
                path.mkdir(parents=True, mode=0o755, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
                with path.open('xb') as f:
                    shutil.copyfileobj(tar.extractfile(member), f)
                path.chmod(0o755 if member.mode & 0o111 else 0o644)
        for directory, _, _ in os.walk(target):
            Path(directory).chmod(0o755)
    (target / 'node_modules').mkdir(mode=0o755, exist_ok=True)
    b.require((target / 'pnpm-lock.yaml').is_file(), 'Frozen dependency lockfile missing')
    records[service] = {'kind': 'bundle', 'path': str(target), 'revision': revision, 'archive_sha256': sha,
                        'fingerprint': source_policy.fingerprint(source_policy.manifest(target))}
    (STATE / 'sources').mkdir(mode=0o700, exist_ok=True)
    b.save(STATE / 'sources' / (w + '.json'), records)
    print(f'{service}: stock commit bundle staged in {w}; credentials and Git metadata excluded.')


def key(path, image):
    h = hashlib.sha256(image.encode())
    b.require((path / 'pnpm-lock.yaml').is_file(), 'Frozen pnpm lockfile missing')
    for filename in INPUTS:
        p = b.safe(path / filename)
        if p.exists():
            h.update(filename.encode() + b'\0' + p.read_bytes())
    return h.hexdigest()


def volume(name_, expected):
    obj = json.loads(b.run(['docker', 'volume', 'inspect', name_]))[0]
    b.require(obj['Driver'] == 'local' and not obj.get('Options') and obj.get('Labels') == expected,
              'Foreign/different dependency volume')
    return obj


def dependency(w, service, frozen=False):
    r = b.read(STATE / 'dependencies' / w / (service + '.json'))
    src = source_record(w, service, frozen=frozen)
    b.require(r['image'] == runtime()['image'] and r['key'] == key(Path(src['path']), r['image']),
              f'Dependency inputs changed; freeze sync, stop apps and run box.py deps --box {__import__("socket").gethostname()} --workspace {w} --service {service} --attempt <new-name> --apply, then select')
    obj = volume(r['volume'], r['labels'])
    b.require((b.safe(Path(obj['Mountpoint'])) / '.chart-installed').read_text() == r['key'], 'Incomplete dependency volume')
    return r


def npm_token(path=None):
    path = b.safe(Path(path) if path is not None else b.ROOT / 'identity/npmrc')
    b.require(path.stat().st_uid == 0 and path.stat().st_mode & 0o077 == 0, 'npmrc must be root-owned mode 0600')
    lines = path.read_text().splitlines()
    b.require(len(lines) == 1 and lines[0].startswith('//registry.npmjs.org/:_authToken='), 'Use the reviewed single-registry npmrc')
    token = lines[0].split('=', 1)[1].strip()
    b.require(token and '${' not in token, 'npm token is missing/unresolved')
    return path, token


def install_deps(w, service, attempt, *, npmrc_path=None, artifact_labels=None):
    name(attempt)
    src = source_record(w, service, frozen=True)
    if src['kind'] == 'mirror':
        b.require(not any(c['State']['Running'] for c in containers()), 'Stop app writers before mirror dependency preparation')
    path = Path(src['path'])
    image = runtime()['image']
    k = key(path, image)
    record = STATE / 'dependencies' / w / (service + '.json')
    if record.exists():
        try:
            r = dependency(w, service)
            print(f'{service}: complete frozen dependencies reused ({r["key"][:12]}).')
            return
        except (RuntimeError, FileNotFoundError):
            if src['kind'] != 'mirror':
                raise RuntimeError('Existing dependency receipt differs; use a new workspace for changed baseline')
            old = b.read(record)
            volume(old['volume'], old['labels'])
            b.require(old['key'] != k or old['image'] != image, 'Dependency volume incomplete; inspect retained state')
    npmrc, token = npm_token(npmrc_path)
    capacity()
    mount = json.loads(b.run(['findmnt', '-J', '-T', '/run', '-o', 'FSTYPE']))['filesystems'][0]
    b.require(mount['fstype'] == 'tmpfs', 'Installer credential projection requires tmpfs /run')
    vol = f'chart-deps-{service}-{k[:12]}-{attempt}'
    labels = artifact_labels if artifact_labels is not None else {
        b.LABEL: OWNER, 'chart-infra.box': __import__('socket').gethostname(), 'chart-infra.key': k}
    present = b.run(['docker', 'volume', 'ls', '-q', '--filter', 'name=^' + vol + '$'])
    b.require(not present, 'Dependency attempt already exists; retain it and choose another --attempt after inspection')
    args = ['docker', 'volume', 'create']
    for label, value in labels.items():
        args += ['--label', label + '=' + value]
    b.run(args + [vol])
    obj = volume(vol, labels)
    modules = b.safe(Path(obj['Mountpoint']))
    b.require(not any(modules.iterdir()), 'Expected empty new volume')
    os.chown(modules, 1000, 1000)
    store = b.safe(Path('/srv/chart/cache/pnpm'))
    b.require(store.is_dir(), 'Prepared package store directory missing')
    if store.stat().st_uid == 0:
        b.require(not any(store.iterdir()), 'Unregistered nonempty package store')
        os.chown(store, 1000, 1000)
    b.require(store.stat().st_uid == 1000, 'Package store ownership differs')
    net = 'chart-preparation'
    existing = b.run(['docker', 'network', 'ls', '-q', '--filter', 'name=^' + net + '$'])
    if not existing:
        b.run(['docker', 'network', 'create', '--label', b.LABEL + '=' + OWNER, net])
    n = json.loads(b.run(['docker', 'network', 'inspect', net]))[0]
    b.require(n['Labels'].get(b.LABEL) == OWNER and not n['Internal'], 'Foreign preparation network')
    logdir = STATE / 'install-logs'
    logdir.mkdir(mode=0o700, exist_ok=True)
    log = logdir / (vol + '.log')
    print(f'{service}: installing frozen dependencies; private redacted log {log}', flush=True)
    with tempfile.TemporaryDirectory(prefix='chart-installer-', dir='/run') as temporary:
        projected = Path(temporary) / 'npmrc'
        b.write(projected, npmrc.read_text(), mode=0o400, uid=1000)
        args = [*b.DOCKER, 'run', '--rm', '--name', vol + '-install', '--label', b.LABEL + '=' + OWNER,
                '--network', net, '--read-only', '--user', '1000:1000', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777',
                '--mount', f'type=bind,src={path},dst=/app,readonly',
                '--mount', f'type=volume,src={vol},dst=/app/node_modules',
                '--mount', f'type=bind,src={store},dst=/srv/chart/cache/pnpm',
                '--mount', f'type=bind,src={projected},dst=/run/npmrc,readonly',
                '-e', 'NPM_CONFIG_USERCONFIG=/run/npmrc', '-e', 'HOME=/tmp', '-e', 'CI=true',
                '-e', 'HUSKY=0', '-e', 'MONGOMS_DISABLE_POSTINSTALL=1', image,
                'pnpm', 'install', '--frozen-lockfile', '--store-dir=/srv/chart/cache/pnpm']
        try:
            result = subprocess.run(args, text=True, capture_output=True, timeout=1200)
        except BaseException:
            ids = b.run(['docker', 'ps', '-q', '--filter', 'name=^/' + vol + '-install$', '--filter', 'label=' + b.LABEL + '=' + OWNER]).split()
            if ids:
                b.run(['docker', 'stop', '--time', '10', *ids])
            raise
        b.write(log, (result.stdout + result.stderr).replace(token, '[redacted]'))
    b.require(result.returncode == 0, f'Dependency install failed; inspect {log}; failed volume retained')
    b.require(source_record(w, service, frozen=True)['fingerprint'] == src['fingerprint'] and key(path, image) == k, 'Source changed during install')
    b.write(modules / '.chart-installed', k, mode=0o444)
    record.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if record.exists():
        history = record.parent / 'history'
        history.mkdir(mode=0o700, exist_ok=True)
        b.save(history / (service + '-' + str(time.time_ns()) + '.json'), b.read(record))
    b.save(record, {'image': image, 'key': k, 'volume': vol, 'labels': labels, 'source': src['fingerprint'], 'log': str(log)})
    print(f'{service}: frozen Linux dependencies prepared; runtime selection unchanged.')


def compose(*args):
    return b.run(['docker', 'compose', '-p', PROJECT, '-f', str(STATE / 'compose.json'), *args], timeout=240)


def containers():
    ids = b.run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + PROJECT]).split()
    if not ids:
        return []
    objects = json.loads(b.run(['docker', 'inspect', *ids]))
    for obj in objects:
        labels = obj['Config'].get('Labels') or {}
        b.require(labels.get(b.LABEL) == OWNER and labels.get('chart-infra.box') == __import__('socket').gethostname(), 'Foreign app container')
    return objects


def spec(inv):
    services = {}
    volumes = {}
    for service, (repo, port, health) in SERVICES.items():
        dep = inv['deps'][service]
        volumes[service + '-deps'] = {'external': True, 'name': dep['volume']}
        probe = "fetch('http://127.0.0.1:" + str(port) + health + "',{signal:AbortSignal.timeout(3000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
        services[service] = {'image': inv['image'], 'pull_policy': 'never', 'user': '1000:1000',
            'read_only': True, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'restart': 'no', 'init': True, 'working_dir': '/app', 'command': ['node', '/run/chart/launch.cjs'],
            'environment': {'HOME': '/tmp'}, 'tmpfs': ['/tmp:rw,nosuid,nodev,mode=1777'],
            'networks': ['runtime'], 'stop_grace_period': '30s',
            'labels': {b.LABEL: OWNER, 'chart-infra.box': inv['box']},
            'volumes': [b.bind(SOURCE / inv['workspace'] / repo, '/app', True),
                        {'type': 'volume', 'source': service + '-deps', 'target': '/app/node_modules', 'read_only': True},
                        b.bind(STATE / service, '/run/chart', True)],
            'healthcheck': {'test': ['CMD', 'node', '-e', probe], 'interval': '5s', 'timeout': '4s', 'retries': 24, 'start_period': '30s'},
            'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}}
        if service in inv.get('sources', {}):
            services[service]['labels']['chart-infra.source'] = inv['sources'][service]
    services['tharamine']['depends_on'] = {'auth': {'condition': 'service_healthy'}}
    services['orange']['depends_on'] = {s: {'condition': 'service_healthy'} for s in ('auth', 'tharamine')}
    return {'name': PROJECT, 'services': services, 'volumes': volumes,
            'networks': {'runtime': {'external': True, 'name': b.PROJECT + '-runtime'}}}


def selection():
    inv = b.read(STATE / 'selection.json')
    b.require(inv['box'] == __import__('socket').gethostname() and inv['owner'] == OWNER, 'Wrong app selection')
    box_config.verify(STATE)
    for service in SERVICES:
        src = source_record(inv['workspace'], service)
        if src['kind'] == 'mirror':
            b.require(inv.get('sources', {}).get(service) == src['registration'],
                      'Mirror not activated; freeze sync and select while apps are stopped')
        b.require(inv['deps'][service] == dependency(inv['workspace'], service), 'Selected dependency generation differs')
    b.require(b.read(STATE / 'compose.json') == spec(inv), 'App Compose contract differs')
    return inv


def select(box, w):
    b.require(not any(c['State']['Running'] for c in containers()), 'Stop app writers before selection')
    box_config.verify(STATE)
    deps = {s: dependency(w, s, frozen=True) for s in SERVICES}
    inv = {'owner': OWNER, 'box': box, 'workspace': w, 'image': runtime()['image'], 'deps': deps}
    sources = {s: r['registration'] for s in SERVICES if (r := workspace(w)[s])['kind'] == 'mirror'}
    if sources:
        inv['sources'] = sources
    if (STATE / 'selection.json').exists():
        previous = b.read(STATE / 'selection.json')
        if previous == inv:
            selection()
            print('Matching app selection retained.')
            return
        history = STATE / 'selections'
        history.mkdir(mode=0o700, exist_ok=True)
        b.save(history / (str(time.time_ns()) + '.json'), previous)
    b.save(STATE / 'compose.json', spec(inv))
    b.save(STATE / 'selection.json', inv)
    print('Prepared workspace selected; no services started.')


def proxy(ip, destination=None):
    units = ('chart-orange.socket', 'chart-orange.service')
    hashes = b.read(STATE / 'proxy-units.json') if (STATE / 'proxy-units.json').exists() else {}
    for unit in units:
        p = Path('/etc/systemd/system') / unit
        if p.exists():
            b.require(b.digest(p) == hashes.get(unit), 'Foreign/edited Orange proxy unit')
    if hashes:
        b.run(['systemctl', 'stop', *units])
    if destination is None:
        return
    import ipaddress
    ipaddress.IPv4Address(destination)
    data = {
        units[0]: '[Unit]\nDescription=Chart Orange Tailscale listener\nRequires=chart-input.service\nAfter=chart-input.service tailscaled.service\nRequiresMountsFor=/srv/chart/data\n'
                  f'[Socket]\nListenStream={ip}:3000\nBindToDevice=tailscale0\nNoDelay=true\n',
        units[1]: '[Unit]\nDescription=Chart Orange TCP forwarding\nRequires=chart-orange.socket\nAfter=chart-orange.socket\nRequiresMountsFor=/srv/chart/data\n'
                  '[Service]\nDynamicUser=true\nUser=chart-orange-proxy\n'
                  f'ExecStart=/usr/lib/systemd/systemd-socket-proxyd {destination}:3000\n'
                  'NoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=true\nRestrictAddressFamilies=AF_INET AF_UNIX\n'}
    archive = STATE / 'proxy-configs'
    archive.mkdir(mode=0o700, exist_ok=True)
    for unit, value in data.items():
        h = hashlib.sha256(value.encode()).hexdigest()
        if not (archive / (unit + '.' + h)).exists():
            b.write(archive / (unit + '.' + h), value)
        b.write(Path('/etc/systemd/system') / unit, value, mode=0o644)
    b.save(STATE / 'proxy-units.json', {u: b.digest(Path('/etc/systemd/system') / u) for u in units})
    b.run(['systemctl', 'daemon-reload'])
    b.run(['systemctl', 'start', units[0]])


def up(inv, backing_inv):
    b.ready()
    proxy(backing_inv['tailscale_ip'])
    try:
        compose('up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '180')
    finally:
        register_clients(inv['box'])
    orange = next(c for c in containers() if c['Config']['Labels']['com.docker.compose.service'] == 'orange')
    proxy(backing_inv['tailscale_ip'], orange['NetworkSettings']['Networks'][b.PROJECT + '-runtime']['IPAddress'])
    print('Auth, Tharamine and Orange healthy; Tailscale API listener ready.')


def register_clients(box):
    b.save(b.STATE / 'app-clients.json', {'owner': OWNER, 'box': box,
           'containers': {c['Id']: c['Config']['Labels']['com.docker.compose.service'] for c in containers()}})


def login_code(email):
    b.require(bool(re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.test', email or '')),
              'Supply the synthetic .test email used in the browser')
    auth = next(c for c in containers() if c['Config']['Labels']['com.docker.compose.service'] == 'auth')
    logs = b.run(['docker', 'logs', '--since', '10m', auth['Id']])
    matches = re.findall(r'sign-in code for ' + re.escape(email.lower()) + r': (\d{6})', logs)
    b.require(matches, 'No recent code found; request a new one in the browser')
    print(matches[-1])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['runtime', 'source', 'deps', 'identity', 'select', 'up', 'stop', 'down', 'status', 'login-code'])
    p.add_argument('--box', required=True)
    p.add_argument('--workspace')
    p.add_argument('--service', choices=list(SERVICES))
    p.add_argument('--image')
    p.add_argument('--archive')
    p.add_argument('--sha256')
    p.add_argument('--revision')
    p.add_argument('--attempt', default='first')
    p.add_argument('--email')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    os.umask(0o077)
    marker = b.guard(a.box)
    with open('/run/lock/chart-backing.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inv = b.inventory(a.box, marker)
        b.ownership(a.box)
        if a.command in ('source', 'deps', 'select'):
            name(a.workspace)
        if a.command in ('source', 'deps'):
            b.require(a.service in SERVICES, 'Select --service')
        if a.command not in ('status', 'login-code') and not a.apply:
            print(f'Plan: {a.command} in {a.box}; data/source retention and offline runtime apply. Add --apply to execute.')
            return
        registry(a.box, create=a.command == 'runtime')
        if a.command == 'runtime': register_runtime(a.image)
        elif a.command == 'source': stage(a.workspace, a.service, a.archive, a.sha256, a.revision)
        elif a.command == 'deps': install_deps(a.workspace, a.service, a.attempt)
        elif a.command == 'identity': box_config.prepare(STATE)
        elif a.command == 'select': select(a.box, a.workspace)
        elif a.command == 'up': up(selection(), inv)
        elif a.command == 'login-code': login_code(a.email)
        elif a.command in ('stop', 'down'):
            containers()
            proxy(inv['tailscale_ip'])
            compose(a.command, '--timeout', '30')
            register_clients(a.box)
            print('Apps stopped; backing services, identities, source and dependency volumes retained.')
        else:
            print(json.dumps({'box': a.box, 'containers': [{'name': c['Name'], 'state': c['State']['Status'],
                  'health': c['State'].get('Health', {}).get('Status')} for c in containers()],
                  'workspaces': {p.stem: {s: {'kind': r['kind'], 'revision': r.get('revision'),
                                  'registration': r.get('registration')} for s, r in b.read(p).items()}
                                 for p in (STATE / 'sources').glob('*.json')},
                  'storage': b.run(['df', '-h', '/', str(b.ROOT)])}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
