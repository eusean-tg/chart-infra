#!/usr/bin/env python3
"""Check a published image and two test-box network boundaries; optionally retire test instances."""
import argparse
import contextlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import socketserver
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import prep as p
import host_common as h
import image
import backup
import artifact_check


PROBE = '''
import json, socket, sys
try:
    with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=3) as s:
        value = s.recv(256).decode()
    print(json.dumps(dict(connected=True, value=value)))
except OSError as e:
    print(json.dumps(dict(connected=False, error=str(e))))
'''

SERVER = r'''
#include <arpa/inet.h>
#include <signal.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
int main(void) {
    alarm(600);
    signal(SIGPIPE, SIG_IGN);
    int s=socket(AF_INET, SOCK_STREAM, 0), yes=1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in a={0}; a.sin_family=AF_INET; a.sin_port=htons(8080);
    a.sin_addr.s_addr=htonl(INADDR_ANY);
    if (s<0 || bind(s,(struct sockaddr*)&a,sizeof(a))<0 || listen(s,16)<0) return 1;
    for (;;) {
        int c=accept(s,0,0); if(c<0) return 2;
        const char *token="TOKEN";
        write(c, token, strlen(token)); close(c);
    }
}
'''


def endpoint(c, name):
    obj = h.owned(c, name); p.require(obj['status'] == 'Running', 'Test box must be running')
    status = json.loads(h.guest(c, name, ['tailscale', 'status', '--json']))
    p.require(status['BackendState'] == 'Running', 'Test box must be enrolled')
    addresses = json.loads(h.guest(c, name, ['ip', '-j', '-4', 'addr', 'show', 'eth0']))
    return {'name': name, 'node_id': status['Self']['ID'],
            'tailnet': next(x for x in status['Self']['TailscaleIPs'] if ':' not in x),
            'bridge': next(a['local'] for row in addresses for a in row['addr_info'] if a['scope'] == 'global')}


def probe(c, source, address, port):
    if source is not None:
        return json.loads(h.guest(c, source, ['python3', '-c', PROBE, address, str(port)]))
    try:
        with socket.create_connection((address, port), timeout=3) as s: value = s.recv(256).decode()
        return {'connected': True, 'value': value}
    except OSError as e: return {'connected': False, 'error': str(e)}


def assert_probe(c, source, address, port, token, allowed):
    result = probe(c, source, address, port)
    if allowed:
        p.require(result.get('connected') and result.get('value') == token, 'Positive network control failed: ' + str((source, address, port, result)))
    else:
        p.require(not result.get('connected'), 'Unexpected underlay access: ' + str((source, address, port)))
    return {'source': source or 'host', 'destination': address, 'port': port, 'allowed': allowed, **result}


@contextlib.contextmanager
def host_listener(token):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self): self.request.sendall(token.encode())
    server = socketserver.ThreadingTCPServer(('0.0.0.0', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield server.server_address[1]
    finally: server.shutdown(); server.server_close(); thread.join()


def network(c, targets, directory, record):
    token = 'chart-boundary-' + str(time.time_ns())
    source = directory / 'server.c'; p.write_owned(source, SERVER.replace('TOKEN', token))
    dockerfile = directory / 'Dockerfile'
    p.write_owned(dockerfile, 'FROM scratch\nCOPY server /server\nENTRYPOINT ["/server"]\n')
    started = []
    port = 38081
    reports = record.setdefault('network', [])
    def check(src, dst, dport, allowed):
        reports.append(assert_probe(c, src, dst, dport, token, allowed))
        h.save(directory / 'proof.json', record)
    try:
        for target in targets:
            name = target['name']; work = '/root/' + token
            print('Testing Docker-published access in ' + name, flush=True)
            h.guest(c, name, ['mkdir', '-m', '700', work])
            h.push(c, name, source, work + '/server.c'); h.push(c, name, dockerfile, work + '/Dockerfile')
            h.guest(c, name, ['gcc', '-static', work + '/server.c', '-o', work + '/server'])
            h.guest(c, name, ['docker', 'build', '--pull=false', '--network=none', '-t', token, work])
            h.guest(c, name, ['docker', 'run', '-d', '--name', token, '--label', 'chart.boundary=' + token,
                              '-p', '0.0.0.0:' + str(port) + ':8080', token])
            started.append(name)
            for attempt in range(15):
                if probe(c, name, '127.0.0.1', port).get('value') == token: break
                time.sleep(1)
            check(name, '127.0.0.1', port, True)
            check(None, target['tailnet'], port, True)
            check(None, target['bridge'], port, False)
        for origin, peer in (targets, targets[::-1]):
            check(origin['name'], peer['tailnet'], port, True)
            check(origin['name'], peer['bridge'], port, False)
        with host_listener(token) as host_port:
            check(None, '127.0.0.1', host_port, True)
            bridge = str(ipaddress.ip_interface(c['bridge_address']).ip)
            check(None, bridge, host_port, True)
            for target in targets: check(target['name'], bridge, host_port, False)
        p.write_owned(directory / 'host-firewall.json', p.run(['nft', '-j', 'list', 'table', 'inet', 'chart_inc_us']))
        for target in targets:
            p.write_owned(directory / (target['name'] + '-guest-firewall.json'),
                          h.guest(c, target['name'], ['nft', '-j', 'list', 'table', 'inet', 'chart_input']))
    finally:
        for name in started:
            label = h.guest(c, name, ['docker', 'inspect', '--format', '{{index .Config.Labels "chart.boundary"}}', token]).strip()
            p.require(label == token, 'Fixture ownership changed; refusing cleanup')
            h.guest(c, name, ['docker', 'rm', '-f', token])


def retire(c, targets, directory, record):
    # Revalidate both targets before any logout or instance removal.
    for target in targets:
        actual = endpoint(c, target['name'])
        p.require(actual == target, 'Test identity/address changed before retirement')
    record['retirement'] = {}; h.save(directory / 'proof.json', record)
    for target in targets:
        name = target['name']
        print('Preserving and retiring test instance ' + name, flush=True)
        record['retirement'][name] = {'phase': 'preserving', 'tailnet_device_id': target['node_id']}
        h.save(directory / 'proof.json', record)
        copy = backup.backup(c, name, rootfs=True)
        record['retirement'][name].update(backup=str(copy), phase='logging-out')
        h.save(directory / 'proof.json', record)
        h.wait_ready(c, name)
        h.guest(c, name, ['tailscale', 'logout'])
        p.run(['incus', 'stop', 'local:' + name, '--project', c['project'], '--timeout', '120'])
        p.require(h.instance(c, name)['status'] == 'Stopped', 'Refusing to delete running test box')
        p.run(['incus', 'config', 'device', 'remove', 'local:' + name, 'data', '--project', c['project']])
        p.run(['incus', 'delete', 'local:' + name, '--project', c['project']])
        record['retirement'][name].update(phase='instance-removed', tailnet_removal='pending authorized console/API removal',
                                         retained_hdd=str(Path(c['boxes_root']) / name))
        h.save(directory / 'proof.json', record)


def run(a):
    c = p.config(a.config); p.check_host(c)
    _, tests = image.names(a.build)
    if not a.apply:
        print(json.dumps({'test_boxes': tests, 'checks': ['exported-image sanitization', 'Docker ingress over Tailscale',
            'host/peer bridge denial with positive controls'], 'retire_tests': a.retire_tests,
            'retention': 'HDD data, stopped recovery rootfs, image and private exports/evidence retained',
            'tailnet_device_removal': 'separate authorized console/API action'}, indent=2)); return
    with p.locked():
        h.host(c)
        build = json.loads((p.STATE / 'bare-images' / a.build / 'build.json').read_text())
        p.require(build.get('owner') == h.OWNER and build.get('tests') == tests and build.get('verified'), 'Expected a verified build')
        recovery = json.loads((p.STATE / 'recovery-proofs' / a.build / 'proof.json').read_text())
        p.require(recovery.get('phase') == 'verified' and recovery.get('image') == build['fingerprint']
                  and recovery.get('box') == tests[0], 'Complete the matching synthetic recovery proof first')
        candidate = h.candidate(c, build['fingerprint'], verified=True)
        for name in tests:
            p.require(h.owned(c, name)['config'].get('volatile.base_image') == build['fingerprint'], 'Wrong test image')
        targets = [endpoint(c, name) for name in tests]
        directory = p.STATE / 'boundary-proofs' / a.build
        p.require(not directory.exists(), 'Boundary proof exists; inspect phase and retained fixtures before retry')
        backup.storage(c, directory, candidate['size'])
        record = {'owner': h.OWNER, 'build': a.build, 'image': build['fingerprint'], 'targets': targets, 'phase': 'artifact'}
        h.save(directory / 'proof.json', record)
        exported = directory / 'export'; exported.mkdir(mode=0o700)
        print('Exporting published image for inspection without booting it', flush=True)
        backup.copy_command(c, ['incus', 'image', 'export', 'local:' + build['fingerprint'], exported, '--project', c['project']])
        archives = list(exported.iterdir())
        p.require(len(archives) == 1 and archives[0].is_file(), 'Expected a unified published-image export')
        record['artifact'] = artifact_check.inspect(archives[0], build['fingerprint'])
        print('Published-image sanitization checks passed', flush=True)
        record['phase'] = 'network'; h.save(directory / 'proof.json', record)
        network(c, targets, directory, record)
        record['phase'] = 'verified'; h.save(directory / 'proof.json', record)
        if a.retire_tests:
            retire(c, targets, directory, record)
            record['phase'] = 'test-instances-retired'; h.save(directory / 'proof.json', record)
        print(json.dumps({'verified': build['fingerprint'], 'evidence': str(directory / 'proof.json'),
                          'network_probes': len(record['network']), 'retirement': record.get('retirement', {}),
                          'pilot': 'untouched', 'external_LAN_k3s_forwarding': 'not packet-tested'}, indent=2))


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True); parser.add_argument('--build', required=True)
    parser.add_argument('--apply', action='store_true'); parser.add_argument('--retire-tests', action='store_true')
    try: run(parser.parse_args())
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
