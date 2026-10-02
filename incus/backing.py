#!/usr/bin/env python3
"""Retained Mongo/Dragonfly backing services inside a prepared Incus box."""
import argparse
import base64
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time

ROOT = Path('/srv/chart/data')
STATE = ROOT / 'identity/backing'
PROJECT = 'chart-backing'
OWNER = 'chart-incus-backing-v1'
LABEL = 'chart-infra.owner'
DOCKER = ['docker', '--host=unix:///var/run/docker.sock']
IMAGES = {
    'mongo': 'docker.io/library/mongo@sha256:609d76574151bee8b148caee83cd9fec8b5f695e5a6db9f1e0f9dced0673574e',
    'redis': 'docker.dragonflydb.io/dragonflydb/dragonfly@sha256:748447aa24ee7d14e28bb9bc2869dc62c1e14396bff6df8c83e8e9a6643eb65d',
}
DATABASES = {'auth': 'auth', 'tharamine': 'orange', 'orange': 'orangeV2', 'fixture': 'chart_fixture'}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(args, stdin=None, timeout=120):
    if args[0] == 'docker':
        args = DOCKER + args[1:]
    p = subprocess.run(args, input=stdin, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        error = p.stderr + p.stdout
        if (STATE / 'credentials.json').exists():
            c = read(STATE / 'credentials.json')
            for value in [c['redis_password'], *(u['password'] for u in c['mongo'].values())]:
                error = error.replace(value, '[redacted]')
        summary = '\n'.join(line[:300] for line in error.splitlines()[:10])
        raise RuntimeError(f'Command failed ({p.returncode}): {" ".join(args[:3])}\n{summary}')
    return p.stdout.strip()


def safe(path):
    require(path.is_absolute() and '..' not in path.parts and path.resolve() == path,
            f'Refusing symlink/traversal: {path}')
    return path


def read(path):
    return json.loads(safe(path).read_text())


def write(path, value, mode=0o600, uid=0):
    safe(path)
    tmp = path.with_name(path.name + '.next')
    safe(tmp)
    require(not tmp.exists(), f'Interrupted write retained at {tmp}; inspect before retry')
    with tmp.open('x') as f:
        os.fchmod(f.fileno(), mode)
        os.fchown(f.fileno(), uid, uid)
        f.write(value)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def save(path, value):
    write(path, json.dumps(value, indent=2) + '\n')


def digest(path):
    return hashlib.sha256(safe(path).read_bytes()).hexdigest()


def guard(box):
    require(os.geteuid() == 0 and socket.gethostname() == box, 'Run as root inside the selected box')
    require(re.fullmatch(r'[a-z][a-z0-9-]{1,40}', box), 'Invalid box name')
    uid = Path('/proc/self/uid_map').read_text().split()
    require(len(uid) == 3 and uid[0] == '0' and int(uid[1]) > 0, 'Expected unprivileged isolated UID mapping')
    safe(ROOT)
    mount = json.loads(run(['findmnt', '-J', '-T', str(ROOT), '-o', 'TARGET,SOURCE,FSTYPE']))['filesystems'][0]
    require(mount['target'] == str(ROOT) and mount['fstype'] == 'ext4'
            and mount['source'].endswith(f'[/shared-dev/boxes/{box}]'), 'Required box HDD attachment missing/different')
    marker = read(ROOT / '.chart-incus-box.json')
    require(marker['owner'] == 'chart-incus-v1' and marker['name'] == box, 'Wrong box data identity')
    require(safe(ROOT / 'identity/guest-prepared').is_file(), 'Guest preparation incomplete')
    require(run(['systemctl', 'is-active', 'chart-input.service']) == 'active', 'Guest input firewall unavailable')
    return marker


def bind(source, target, readonly=False):
    return {'type': 'bind', 'source': str(source), 'target': target,
            'read_only': readonly, 'bind': {'create_host_path': False}}


def compose_spec(inv):
    data = ROOT / 'datasets' / inv['dataset']
    common = {'restart': 'no', 'user': '999:999', 'read_only': True,
              'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
              'tmpfs': ['/tmp:rw,nosuid,nodev,mode=1777'], 'networks': ['runtime'],
              'labels': {LABEL: OWNER, 'chart-infra.box': inv['box']},
              'stop_grace_period': '60s', 'pull_policy': 'never',
              'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}}
    mongo = {**common, 'image': IMAGES['mongo'], 'hostname': 'mongo',
             'ulimits': {'nofile': {'soft': 64000, 'hard': 64000}},
             'entrypoint': ['mongod'],
             'command': ['--bind_ip_all', '--port', '27017', '--replSet', 'rs0', '--auth',
                         '--keyFile', '/run/chart/keyfile', '--dbpath', '/data/db',
                         '--wiredTigerCacheSizeGB', '0.5', '--oplogSize', '256'],
             'volumes': [bind(data / 'mongo/member-0', '/data/db'),
                         bind(STATE / 'keyfile', '/run/chart/keyfile', True)],
             'healthcheck': {'test': ['CMD', 'mongosh', '--quiet', '--eval',
                                      'quit(db.hello().isWritablePrimary ? 0 : 1)'],
                             'interval': '10s', 'timeout': '5s', 'retries': 12, 'start_period': '20s'}}
    redis = {**common, 'image': IMAGES['redis'], 'entrypoint': ['dragonfly'],
             'command': ['--flagfile=/run/chart/dragonfly.flags'],
             'healthcheck': {'test': ['CMD-SHELL', 'export REDISCLI_AUTH=$$(sed -n "s/^--requirepass=//p" /run/chart/dragonfly.flags); test "$$(redis-cli ping)" = PONG'],
                             'interval': '10s', 'timeout': '5s', 'retries': 6},
             'volumes': [bind(data / 'dragonfly', '/data'),
                         bind(STATE / 'dragonfly.flags', '/run/chart/dragonfly.flags', True)]}
    return {'name': PROJECT, 'services': {'mongo': mongo, 'redis': redis},
            'networks': {'runtime': {'name': PROJECT + '-runtime', 'internal': True,
                                    'labels': {LABEL: OWNER, 'chart-infra.box': inv['box']}}}}


def inventory(box, marker):
    inv = read(STATE / 'inventory.json')
    require(inv['owner'] == OWNER and inv['box'] == box and inv['box_marker'] == marker, 'Backing identity differs')
    require(inv['images'] == IMAGES, 'Image pins differ; explicit migration required')
    data = safe(ROOT / 'datasets' / inv['dataset'])
    require(re.fullmatch(r'[a-z][a-z0-9-]{0,31}', inv['dataset']), 'Invalid dataset')
    require((safe(data / '.chart-dataset')).read_text() == inv['dataset_marker'], 'Dataset identity missing/different')
    for p in (data / 'mongo/member-0', data / 'dragonfly'):
        require(safe(p).is_dir(), 'Dataset directory missing; refusing empty replacement')
    require(inv['phase'] in ('prepared', 'initializing', 'ready'), 'Invalid initialization phase')
    if inv['phase'] == 'ready':
        require(safe(data / 'mongo/member-0/WiredTiger').is_file(), 'Initialized Mongo data missing; refusing replacement')
    for name, expected in inv['identity_hashes'].items():
        require(digest(STATE / name) == expected, f'Retained identity/config differs: {name}')
    require(read(STATE / 'compose.json') == compose_spec(inv), 'Compose contract differs; explicit configuration update required')
    return inv


def ownership(box):
    ids = run(['docker', 'ps', '-aq', '--no-trunc', '--filter', f'label=com.docker.compose.project={PROJECT}']).split()
    for ident in ids:
        obj = json.loads(run(['docker', 'inspect', ident]))[0]
        labels = obj['Config'].get('Labels') or {}
        require(labels.get(LABEL) == OWNER and labels.get('chart-infra.box') == box,
                'Foreign Compose container; refusing mutation')
    clients = registered_clients(box)
    networks = run(['docker', 'network', 'ls', '-q', '--filter', 'name=^' + PROJECT + '-runtime$']).split()
    for ident in networks:
        obj = json.loads(run(['docker', 'network', 'inspect', ident]))[0]
        require(obj['Internal'] and obj['Labels'].get(LABEL) == OWNER
                and obj['Labels'].get('chart-infra.box') == box, 'Foreign/externally connected runtime network')
        require(set(obj.get('Containers', {})).issubset(set(ids) | {c['Id'] for c in clients}),
                'Other runtime clients are attached; stop them before backing lifecycle operations')
    return clients


def registered_clients(box):
    path = STATE / 'app-clients.json'
    if not path.exists():
        return []
    record = read(path)
    require(record['owner'] == 'chart-incus-apps-v1' and record['box'] == box, 'Wrong app-client registration')
    existing = set(run(['docker', 'ps', '-aq', '--no-trunc']).split())
    result = []
    for ident, service in record['containers'].items():
        require(re.fullmatch(r'[a-f0-9]{64}', ident) and service in ('auth', 'tharamine', 'orange'), 'Invalid app-client identity')
        if ident not in existing:
            continue
        obj = json.loads(run(['docker', 'inspect', ident]))[0]
        labels = obj['Config'].get('Labels') or {}
        require(labels.get(LABEL) == 'chart-incus-apps-v1' and labels.get('chart-infra.box') == box
                and labels.get('com.docker.compose.project') == 'chart-apps'
                and labels.get('com.docker.compose.service') == service, 'Foreign registered app client')
        require(set(obj['NetworkSettings']['Networks']) == {PROJECT + '-runtime'}, 'App client attached to another network')
        result.append(obj)
    return result


def compose(*args):
    return run(['docker', 'compose', '-p', PROJECT, '-f', str(STATE / 'compose.json'), *args], timeout=240)


def prepare(box, marker, dataset, ip):
    require(dataset and re.fullmatch(r'[a-z][a-z0-9-]{0,31}', dataset), 'Supply a simple --dataset name')
    require(ip and ipaddress.ip_address(ip) in ipaddress.ip_network('100.64.0.0/10'), 'Expected Tailscale IPv4')
    require(ip in run(['tailscale', 'ip', '-4']).split(), 'Tailscale address not assigned to this box')
    if STATE.exists():
        inv = inventory(box, marker)
        require(inv['dataset'] == dataset and inv['tailscale_ip'] == ip, 'Prepared selection differs; no implicit switch')
        print('Existing backing identity and dataset retained.')
        return
    data = safe(ROOT / 'datasets' / dataset)
    require(not data.exists(), 'Dataset path exists without backing inventory; inspect, never overwrite')
    safe(STATE).mkdir(mode=0o700)
    data.mkdir(mode=0o700)
    (data / 'mongo').mkdir(mode=0o700)
    for p in (data / 'mongo/member-0', data / 'dragonfly'):
        p.mkdir(mode=0o700)
        os.chown(p, 999, 999)
    token = secrets.token_hex(24)
    write(data / '.chart-dataset', token)
    users = {name: {'user': f'{box.replace("-", "_")}_{name}', 'password': secrets.token_hex(24), 'db': database}
             for name, database in DATABASES.items()}
    users['admin'] = {'user': 'box_admin', 'password': secrets.token_hex(24), 'db': 'admin'}
    creds = {'mongo': users, 'redis_password': secrets.token_hex(24)}
    save(STATE / 'credentials.json', creds)
    write(STATE / 'keyfile', base64.b64encode(secrets.token_bytes(384)).decode() + '\n', 0o400, 999)
    write(STATE / 'dragonfly.flags', '--logtostderr=true\n--cache_mode=true\n--proactor_threads=1\n'
          '--dir=/data\n--dbfilename=dump\n--requirepass=' + creds['redis_password'] + '\n', 0o400, 999)
    inv = {'owner': OWNER, 'box': box, 'box_marker': marker, 'dataset': dataset,
           'dataset_marker': token, 'tailscale_ip': ip, 'images': IMAGES, 'phase': 'prepared',
           'identity_hashes': {name: digest(STATE / name) for name in ('credentials.json', 'keyfile', 'dragonfly.flags')}}
    save(STATE / 'compose.json', compose_spec(inv))
    save(STATE / 'inventory.json', inv)
    print('Prepared empty HDD dataset and independent box credentials; no services started.')


def mongo_js(code, auth=True):
    prefix = ''
    if auth:
        c = read(STATE / 'credentials.json')['mongo']['admin']
        prefix = 'assert(db.getSiblingDB("admin").auth(' + json.dumps(c['user']) + ',' + json.dumps(c['password']) + '));\n'
    return run(
        ['docker', 'compose', '-p', PROJECT, '-f', str(STATE / 'compose.json'), 'exec', '-T', 'mongo',
         'mongosh', 'mongodb://127.0.0.1:27017/admin?directConnection=true&serverSelectionTimeoutMS=2000&connectTimeoutMS=2000',
         '--quiet', '--norc', '--file', '/dev/stdin'], stdin=prefix + code + '\n', timeout=90)


def initialize(inv):
    creds = read(STATE / 'credentials.json')['mongo']
    authenticated = False
    try:
        mongo_js('assert.equal(db.adminCommand({ping:1}).ok,1);')
        authenticated = True
    except RuntimeError:
        pass
    if not authenticated:
        mongo_js('const h=db.hello(); if(!h.setName){ assert.equal(rs.initiate({_id:"rs0",members:[{_id:0,host:"mongo:27017"}]}).ok,1); }', False)
        mongo_js('for(let i=0;i<120&&!db.hello().isWritablePrimary;i++){sleep(500);} assert(db.hello().isWritablePrimary);', False)
        a = creds['admin']
        code = 'db.getSiblingDB("admin").createUser(' + json.dumps({'user': a['user'], 'pwd': a['password'], 'roles': ['root']}) + ');'
        # First-user creation uses the localhost exception; no unauthenticated network listener is enabled.
        run(['docker', 'compose', '-p', PROJECT, '-f', str(STATE / 'compose.json'), 'exec', '-T', 'mongo',
             'mongosh', '--quiet', '--norc', '--file', '/dev/stdin'], stdin=code + '\n', timeout=90)
    code = 'const a=db.getSiblingDB("admin");\n'
    for name, c in creds.items():
        if name == 'admin':
            continue
        spec = {'user': c['user'], 'pwd': c['password'], 'roles': [{'role': 'readWrite', 'db': c['db']}]}
        code += 'if(!a.getUser(' + json.dumps(c['user']) + ')){a.createUser(' + json.dumps(spec) + ');}\n'
    mongo_js(code)
    mongo_js('assert.equal(rs.conf().members.length,1); assert.equal(rs.conf().members[0].host,"mongo:27017");')
    inv['phase'] = 'ready'
    save(STATE / 'inventory.json', inv)


def container(service):
    ident = compose('ps', '-q', service)
    require(bool(ident), f'{service} is not running')
    return json.loads(run(['docker', 'inspect', ident]))[0]


def redis_command(*args, authenticated=True):
    obj = container('redis')
    ip = obj['NetworkSettings']['Networks'][PROJECT + '-runtime']['IPAddress']
    def request(sock, values):
        payload = f'*{len(values)}\r\n'.encode()
        for value in values:
            b = str(value).encode()
            payload += f'${len(b)}\r\n'.encode() + b + b'\r\n'
        sock.sendall(payload)
        f = sock.makefile('rb')
        line = f.readline()
        require(bool(line), 'Empty Redis response')
        if line[:1] == b'-':
            raise RuntimeError('Redis returned an error')
        if line[:1] == b'$':
            size = int(line[1:])
            if size == -1:
                return None
            b = f.read(size)
            require(f.read(2) == b'\r\n', 'Invalid Redis response')
            return b.decode()
        return line[1:].decode().strip()
    with socket.create_connection((ip, 6379), timeout=20) as sock:
        if authenticated:
            request(sock, ['AUTH', read(STATE / 'credentials.json')['redis_password']])
        return request(sock, args)


def ready():
    mongo_js('assert.equal(db.hello().isWritablePrimary,true);')
    require(redis_command('PING') == 'PONG', 'Cache not ready')


def proxy(inv, start):
    """Expose plain Mongo TCP on the box Tailscale interface without adding DB egress."""
    units = ['chart-mongo.socket', 'chart-mongo.service']
    hashes = read(STATE / 'proxy-units.json') if (STATE / 'proxy-units.json').exists() else {}
    for unit in units:
        p = safe(Path('/etc/systemd/system') / unit)
        if p.exists():
            require(digest(p) == hashes.get(unit), 'Foreign or edited Mongo proxy unit')
    if hashes:
        run(['systemctl', 'stop', *units])
    if not start:
        return
    executable = '/usr/lib/systemd/systemd-socket-proxyd'
    require(Path(executable).is_file(), 'systemd TCP proxy unavailable')
    ip = container('mongo')['NetworkSettings']['Networks'][PROJECT + '-runtime']['IPAddress']
    ipaddress.IPv4Address(ip)
    contents = {
        'chart-mongo.socket': '[Unit]\nDescription=Chart Mongo Tailscale listener\n'
        'After=tailscaled.service chart-input.service\nRequires=chart-input.service\n'
        'RequiresMountsFor=/srv/chart/data\n'
        f'[Socket]\nListenStream={inv["tailscale_ip"]}:27017\nBindToDevice=tailscale0\nNoDelay=true\n',
        'chart-mongo.service': '[Unit]\nDescription=Chart Mongo TCP forwarding\nRequires=chart-mongo.socket\n'
        'After=chart-mongo.socket\nRequiresMountsFor=/srv/chart/data\n[Service]\n'
        'DynamicUser=true\nUser=chart-mongo-proxy\n'
        f'ExecStart={executable} {ip}:27017\nNoNewPrivileges=true\nPrivateTmp=true\n'
        'ProtectSystem=strict\nProtectHome=true\nRestrictAddressFamilies=AF_INET AF_UNIX\n'
    }
    archive = STATE / 'proxy-configs'
    safe(archive).mkdir(mode=0o700, exist_ok=True)
    for unit, value in contents.items():
        h = hashlib.sha256(value.encode()).hexdigest()
        retained = archive / (unit + '.' + h)
        if not retained.exists():
            write(retained, value)
        write(Path('/etc/systemd/system') / unit, value, 0o644)
    save(STATE / 'proxy-units.json', {u: digest(Path('/etc/systemd/system') / u) for u in units})
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'start', 'chart-mongo.socket'])


def up(inv):
    require(inv['tailscale_ip'] in run(['tailscale', 'ip', '-4']).split(), 'Tailscale address changed')
    for image in IMAGES.values():
        run(['docker', 'image', 'inspect', image])
    proxy(inv, False)
    if inv['phase'] == 'prepared':
        inv['phase'] = 'initializing'
        save(STATE / 'inventory.json', inv)
    compose('up', '-d', '--pull', 'never')
    for _ in range(60):
        try:
            mongo_js('assert.equal(db.adminCommand({ping:1}).ok,1);', False)
            break
        except RuntimeError:
            time.sleep(1)
    else:
        raise RuntimeError('Mongo startup timeout; data retained')
    if inv['phase'] != 'ready':
        initialize(inv)
    deadline = time.monotonic() + 60
    while True:
        try:
            ready()
            require(all(container(s)['State'].get('Health', {}).get('Status') == 'healthy'
                        for s in ('mongo', 'redis')), 'Waiting for service health checks')
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    proxy(inv, True)
    print('Mongo PRIMARY and authenticated Dragonfly ready; data and identities retained.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'fetch', 'up', 'stop', 'down', 'status'])
    p.add_argument('--box', required=True)
    p.add_argument('--dataset')
    p.add_argument('--tailscale-ip')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    os.umask(0o077)
    marker = guard(a.box)
    with open('/run/lock/chart-backing.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        clients = ownership(a.box)
        if a.command == 'status':
            inv = inventory(a.box, marker)
            print(json.dumps({'box': a.box, 'dataset': inv['dataset'], 'phase': inv['phase'],
                              'images': inv['images'], 'services': compose('ps', '-a'),
                              'storage': run(['df', '-h', '/', str(ROOT)])}, indent=2))
        elif not a.apply:
            if a.command not in ('prepare', 'fetch'):
                inventory(a.box, marker)
            print(f'Plan: {a.command} backing services in {a.box}; no source fetching or data deletion. Add --apply to execute.')
        elif a.command == 'prepare':
            prepare(a.box, marker, a.dataset, a.tailscale_ip)
        elif a.command == 'fetch':
            for image in IMAGES.values():
                run(['docker', 'pull', image], timeout=900)
            print('Pinned public images fetched; no deployment change.')
        else:
            inv = inventory(a.box, marker)
            require(not any(c['State']['Running'] for c in clients),
                    f'Stop app writers first: python3 /opt/chart-infra/incus/box.py stop --box {a.box} --apply')
            if a.command == 'down':
                require(not clients,
                        f'Remove app containers first: python3 /opt/chart-infra/incus/box.py down --box {a.box} --apply; retained data is preserved')
            if a.command == 'up':
                up(inv)
            else:
                if compose('ps', '-q', 'redis'):
                    require(redis_command('SAVE') == 'OK', 'Cache snapshot failed; refusing teardown')
                proxy(inv, False)
                compose(a.command, '--timeout', '60')
                print('Services stopped; dataset, cache snapshot and identities retained.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
