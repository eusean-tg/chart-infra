"""Plain Tailscale IP:port routes for chart Mongo profiles; no TLS, SNI or DNS."""
import hashlib
import ipaddress
import json
import socket
import subprocess
import time

from common import STATE, guard, load, lock, run, save

IMAGE = 'haproxy@sha256:6343ce34a132a5dceaa24767d739df2bd519f8f7c1079ae39e4821334e8eb42e'
NAME = 'chart-infra-access'


def inspect(name):
    p = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
    if p.returncode:
        return None
    o = json.loads(p.stdout)[0]
    if o['Config'].get('Labels', {}).get('chart-infra') != 'access':
        raise SystemExit('Refusing unowned proxy container')
    return o


def set_route(profile, route):
    guard()
    held = lock('chart-access')
    path = STATE / 'access-routes.json'
    previous = load('access-routes.json') if path.exists() else {}
    desired = dict(previous)
    if route is None:
        desired.pop(profile, None)
    else:
        desired[profile] = route
    host = str(ipaddress.IPv4Address(load('host.json')['tailscale']))
    ts = json.loads(run(['tailscale', 'status', '--json'], capture=True))
    if host not in ts.get('TailscaleIPs', []) or ts.get('BackendState') != 'Running':
        raise SystemExit('Registered Tailscale IP is not active')
    old = inspect(NAME)
    if not desired:
        if old:
            run(['docker', 'rm', '-f', NAME])
        save('access-routes.json', desired)
        return
    ports = [r['port'] for r in desired.values()]
    if len(ports) != len(set(ports)):
        raise SystemExit('Duplicate external Mongo port')
    old_ports = {r['port'] for r in previous.values()} if old and old['State']['Running'] else set()
    for port in ports:
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise SystemExit('Invalid external port')
        if port not in old_ports:
            with socket.socket() as sock:
                # Match HAProxy's reuse behavior after withdrawing/re-adding a route:
                # closed client connections may leave the previous port in TIME_WAIT.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
    lines = ['global', '  maxconn 256', '  nbthread 1', 'defaults', '  mode tcp',
             '  timeout connect 5s', '  timeout client 1h', '  timeout server 1h']
    for index, (name, r) in enumerate(sorted(desired.items())):
        address = str(ipaddress.IPv4Address(r['address']))
        target_port = r.get("target_port", 27017)
        if not isinstance(target_port, int) or not 1 <= target_port <= 65535:
            raise SystemExit("Invalid target port")
        lines += [f'listen mongo_{index}', f'  bind {host}:{r["port"]}',
                  '  tcp-request connection reject if !{ src 100.64.0.0/10 }',
                  f'  server backing {address}:{target_port} check inter 5s fall 3 rise 2']
    config = '\n'.join(lines) + '\n'
    digest = hashlib.sha256(config.encode()).hexdigest()
    release = STATE / 'access-releases' / digest
    release.mkdir(parents=True, exist_ok=True)
    cfg = release / 'haproxy.cfg'
    if not cfg.exists():
        cfg.write_text(config)
        cfg.chmod(0o644)
    mount = f'type=bind,src={cfg},dst=/usr/local/etc/haproxy/haproxy.cfg,readonly'
    run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
         '--mount', mount, IMAGE, 'haproxy', '-c', '-f', '/usr/local/etc/haproxy/haproxy.cfg'])
    if old and old['Config'].get('Labels', {}).get('config-sha256') == digest and old['State']['Running']:
        save('access-routes.json', desired)
        return
    backup = NAME + '-previous-' + str(time.time_ns())
    if old:
        run(['docker', 'stop', '--timeout', '10', NAME])
        run(['docker', 'rename', NAME, backup])
    try:
        run(['docker', 'run', '-d', '--name', NAME, '--restart', 'unless-stopped',
             '--network', 'host', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
             '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3',
             '--label', 'chart-infra=access', '--label', 'config-sha256=' + digest, '--mount', mount, IMAGE])
        for port in ports:
            for attempt in range(20):
                try:
                    with socket.create_connection((host, port), timeout=1):
                        break
                except OSError:
                    if attempt == 19: raise
                    time.sleep(0.25)
    except BaseException:
        if inspect(NAME): run(['docker', 'rm', '-f', NAME])
        if old:
            run(['docker', 'rename', backup, NAME])
            run(['docker', 'start', NAME])
        raise
    if old:
        run(['docker', 'rm', backup])
    save('access-routes.json', desired)
    save('access.json', {'host': host, 'image': IMAGE, 'config_sha256': digest, 'routes': desired})
