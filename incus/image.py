#!/usr/bin/env python3
"""Build and verify an application-free Ubuntu developer-box image."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import urllib.request
import prep as p
import host_common as h

PINS = json.loads((p.HERE / 'bare.lock.json').read_text())
FILES = ('bare-base.sh', 'bare-identity.sh', 'guest-firewall.sh', 'bare.lock.json', 'versions.lock.json')


def artifacts(path, fetch=False):
    root = Path(path).expanduser().absolute(); p.no_symlinks(root)
    if fetch: root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, pin in PINS['nvm']['files'].items():
        target = root / name; p.no_symlinks(target)
        if fetch and not target.exists():
            data = urllib.request.urlopen(pin['url'], timeout=60).read()
            p.require(hashlib.sha256(data).hexdigest() == pin['sha256'], 'Nvm checksum differs')
            with target.open('xb') as f: f.write(data)
        p.require(p.digest(target) == pin['sha256'], 'Nvm artifact differs: ' + name)
    return root


def names(build):
    p.require(re.fullmatch('[a-z][a-z0-9-]{0,19}', build), 'Use a build name up to 20 lowercase characters')
    return 'chart-bare-' + build, ['chart-test-' + build + '-' + s for s in ('a', 'b')]


def provision(c, name, keyfile, scripts, adopt=False):
    h.wait_ready(c, name)
    h.guest(c, name, ['install', '-d', '-m', '700', '/root/chart-prep'])
    h.push(c, name, scripts / 'bare-identity.sh', '/root/chart-prep/bare-identity.sh')
    h.push(c, name, keyfile, '/root/chart-prep/authorized_keys')
    timezone = p.run(['timedatectl', 'show', '--property=Timezone', '--value']).strip()
    return h.guest(c, name, ['env', 'CHART_ADOPT_DATA=' + ('1' if adopt else '0'),
                           'bash', '/root/chart-prep/bare-identity.sh', timezone])


def create(c, name, fingerprint, keyfile, scripts, adopt=False):
    p.box_name(name)
    p.require(not p.capacity(c)['review'], 'Review storage capacity before creating a box')
    p.require(not any(i['name'] == name for i in p.query('/1.0/instances?project=' + c['project'] + '&recursion=1')), 'Instance name exists')
    target = Path(c['boxes_root']) / name; p.no_symlinks(target)
    marker = {'owner': p.OWNER, 'name': name, 'machine_id': c['machine_id'], 'hdd_uuid': c['hdd_uuid']}
    if adopt:
        p.require(json.loads((target / '.chart-incus-box.json').read_text()) == marker, 'Adoption marker differs')
    else:
        p.require(not target.exists(), 'Retained data exists; use explicit recreation/adoption')
        target.mkdir(mode=0o700)
        h.save(target / '.chart-incus-box.json', marker)
    spec = p.instance_spec(c, name); spec['source']['fingerprint'] = fingerprint
    spec['config']['user.chart-box'] = h.OWNER
    p.query('/1.0/instances?project=' + c['project'], spec, 'POST')
    h.owned(c, name)
    p.run(['incus', 'start', 'local:' + name, '--project', c['project']])
    output = provision(c, name, keyfile, scripts, adopt)
    print(output, end='')
    return output


def build(a):
    c = p.config(a.config); p.check_host(c)
    builder, tests = names(a.build)
    base = p.artifacts(a.artifacts); nvm = artifacts(a.nvm_artifacts)
    key = p.public_key(a.ssh_key)
    print(json.dumps({'builder': builder, 'test_boxes': tests, 'contents': 'Ubuntu, Docker/Compose, nvm, build tools, SSH, unenrolled Tailscale',
                      'source_or_service_images': False, 'apply': a.apply}, indent=2), flush=True)
    if not a.apply: return
    with p.locked():
        h.host(c)
        p.require(not p.capacity(c)['review'], 'Review SSD capacity before build')
        git = ['git', '-c', 'safe.directory=' + str(p.HERE.parent), '-C', p.HERE.parent]
        p.require(not p.run([*git, 'status', '--porcelain']).strip(), 'Commit reviewed helpers before building')
        commit = p.run([*git, 'rev-parse', 'HEAD']).strip()
        directory = p.STATE / 'bare-images' / a.build
        p.require(not directory.exists(), 'Retained build exists; inspect and use another build name')
        existing = p.query('/1.0/instances?project=' + c['project'] + '&recursion=1')
        p.require(not any(i['name'] in [builder, *tests] for i in existing), 'Instance name collision')
        directory.mkdir(parents=True, mode=0o700)
        scripts = directory / 'scripts'
        for name in FILES:
            p.write_owned(scripts / name, p.run([*git, 'show', commit + ':incus/' + name]), mode=0o600)
        p.write_owned(directory / 'authorized_key', key)
        record = {'owner': h.OWNER, 'build': a.build, 'commit': commit, 'builder': builder, 'tests': tests, 'phase': 'building'}
        h.save(directory / 'build.json', record)
        images = p.query('/1.0/images?project=' + c['project'] + '&recursion=1')
        if not any(i['fingerprint'] == p.PINS['image']['fingerprint'] for i in images):
            p.run(['incus', 'image', 'import', base / 'incus.tar.xz', base / 'rootfs.tar.xz', 'local:', '--project', c['project']])
        spec = p.instance_spec(c, builder)
        del spec['devices']['data']; del spec['devices']['tun']
        p.query('/1.0/instances?project=' + c['project'], spec, 'POST')
        p.run(['incus', 'start', 'local:' + builder, '--project', c['project']])
        h.wait_ready(c, builder)
        h.guest(c, builder, ['install', '-d', '-m', '700', '/root/chart-prep'])
        h.guest(c, builder, ['touch', '/var/lib/chart-bare-builder'])
        for name in ('bare-base.sh', 'guest-firewall.sh'): h.push(c, builder, scripts / name, '/root/chart-prep/' + name)
        h.push(c, builder, base / 'tailscale.deb', '/root/chart-prep/tailscale.deb')
        for name in PINS['nvm']['files']: h.push(c, builder, nvm / name, '/root/chart-prep/' + name)
        packages = {n: p.PINS['packages'][n] for n in ('docker.io', 'docker-compose-v2', 'openssh-server', 'nftables', 'iptables')}
        packages.update(PINS['packages'])
        print('Installing generic box tools in ' + builder, flush=True)
        output = h.guest(c, builder, ['env', 'DEBIAN_FRONTEND=noninteractive', 'bash', '/root/chart-prep/bare-base.sh',
                                    *(n + '=' + v for n, v in packages.items())])
        p.write_owned(directory / 'build.log', output)
        p.write_owned(directory / 'packages.tsv', h.guest(c, builder, ['cat', '/var/lib/chart-bare-packages.tsv']))
        p.run(['incus', 'stop', 'local:' + builder, '--project', c['project']])
        p.run(['incus', 'publish', 'local:' + builder, 'local:', '--project', c['project'],
               'chart.owner=' + h.OWNER, 'chart.build=' + a.build, 'chart.commit=' + commit])
        images = p.query('/1.0/images?project=' + c['project'] + '&recursion=1')
        matches = [i for i in images if i.get('properties', {}).get('chart.build') == a.build and i.get('properties', {}).get('chart.owner') == h.OWNER]
        p.require(len(matches) == 1 and not matches[0]['public'], 'Unique private candidate missing')
        record.update(fingerprint=matches[0]['fingerprint'], phase='acceptance-pending')
        h.save(directory / 'build.json', record)
        for name in tests:
            create(c, name, record['fingerprint'], directory / 'authorized_key', scripts)
            print(f'sudo incus exec local:{name} --project {c["project"]} -- tailscale up --hostname={name} --accept-routes=false --accept-dns=true --ssh=false')
        print('Enroll the two test boxes, then run image-verify. No alias moved; no existing box changed.')


def resume(a):
    c = p.config(a.config); p.check_host(c)
    builder, tests = names(a.build)
    if not a.apply:
        print('Plan: finish identity provisioning of the recorded image test boxes; retain image and data.'); return
    with p.locked():
        h.host(c)
        directory = p.STATE / 'bare-images' / a.build
        record = json.loads((directory / 'build.json').read_text())
        p.require(record.get('owner') == h.OWNER and record.get('build') == a.build
                  and record.get('builder') == builder and record.get('tests') == tests
                  and record.get('phase') == 'acceptance-pending', 'Not a resumable published build')
        candidate = h.candidate(c, record['fingerprint'])
        p.require(candidate['properties'].get('chart.build') == a.build
                  and candidate['properties'].get('chart.commit') == record['commit'], 'Candidate provenance differs')
        scripts = directory / 'scripts'
        git = ['git', '-c', 'safe.directory=' + str(p.HERE.parent), '-C', p.HERE.parent]
        for name in FILES:
            p.require((scripts / name).read_text() == p.run([*git, 'show', record['commit'] + ':incus/' + name]),
                      'Recorded provisioning script differs: ' + name)
        p.public_key(directory / 'authorized_key')
        existing = {obj['name'] for obj in p.query('/1.0/instances?project=' + c['project'] + '&recursion=1')}
        for name in tests:
            if name not in existing:
                create(c, name, record['fingerprint'], directory / 'authorized_key', scripts)
            else:
                obj = h.owned(c, name)
                p.require(obj['config'].get('volatile.base_image') == record['fingerprint'], 'Wrong test image')
                p.require(obj['status'] in ('Running', 'Stopped'), 'Unexpected test box state')
                if obj['status'] == 'Stopped':
                    p.run(['incus', 'start', 'local:' + name, '--project', c['project']])
                h.wait_ready(c, name)
                state = h.guest(c, name, ['sh', '-ec',
                    'if test -e /srv/chart/data/identity/guest-prepared; then echo prepared; '
                    'elif test -e /srv/chart/data/identity; then echo partial; else echo fresh; fi']).strip()
                p.require(state in ('fresh', 'prepared'), 'Partial identity in ' + name + '; inspect without regenerating keys')
                if state == 'fresh':
                    print(provision(c, name, directory / 'authorized_key', scripts), end='')
                else:
                    print(name + ': existing prepared identity retained')
            print(f'sudo incus exec local:{name} --project {c["project"]} -- tailscale up --hostname={name} --accept-routes=false --accept-dns=true --ssh=false')
        print('Test boxes prepared. Enroll both, then run image-verify; the image is not yet accepted.')


def verify(a):
    c = p.config(a.config); p.check_host(c); names(a.build)
    if not a.apply:
        print('Plan: generic tool, identity, empty-source/image and Tailscale/bridge checks in both enrolled test boxes.'); return
    with p.locked():
        h.host(c)
        directory = p.STATE / 'bare-images' / a.build
        record = json.loads((directory / 'build.json').read_text())
        p.require(record['owner'] == h.OWNER and record['phase'] == 'acceptance-pending', 'Not an unverified candidate')
        h.candidate(c, record['fingerprint'])
        for name in record['tests']:
            h.owned(c, name)
            ts = json.loads(h.guest(c, name, ['tailscale', 'status', '--json']))
            p.require(ts['BackendState'] == 'Running', 'Enroll both test boxes before verification')
        reports = []
        for name in record['tests']:
            obj = h.owned(c, name)
            p.require(obj['config'].get('volatile.base_image') == record['fingerprint'], 'Wrong test image')
            if name not in record.get('runtime_verified', []):
                # No registry or application access is required for these OS checks.
                h.guest(c, name, ['bash', '-ec', '. /etc/profile.d/chart-nvm.sh; test "$(nvm --version)" = 0.40.3; docker info >/dev/null; docker compose version; command -v git curl jq python3 gcc make; test -z "$(ls -A /srv/chart/source)"; test -z "$(docker image ls -q)"; test -z "$(docker ps -aq)"; test -z "$(docker volume ls -q)"; systemctl is-active chart-input.service; nft list table inet chart_input'])
                h.guest(c, name, ['bash', '-ec', 'install -d -m 700 /root/chart-runtime-proof; cd /root/chart-runtime-proof; printf "int main(void) { return 0; }\\n" > main.c; gcc -static main.c -o check; printf "FROM scratch\\nCOPY check /check\\nENTRYPOINT [\\\"/check\\\"]\\n" > Dockerfile; docker build --network=none -t chart-bare-runtime-proof .; docker run --name chart-bare-runtime-proof --network=none chart-bare-runtime-proof'])
                record.setdefault('runtime_verified', []).append(name)
                h.save(directory / 'build.json', record)
            ts = json.loads(h.guest(c, name, ['tailscale', 'status', '--json']))
            p.require(ts['BackendState'] == 'Running', 'Enroll test box first')
            ip = next(x for x in ts['Self']['TailscaleIPs'] if ':' not in x)
            rows = json.loads(h.guest(c, name, ['ip', '-j', '-4', 'addr', 'show', 'eth0']))
            bridge = next(x['local'] for row in rows for x in row['addr_info'] if x['scope'] == 'global')
            with socket.create_connection((ip, 22), timeout=5): pass
            try: connection = socket.create_connection((bridge, 22), timeout=2)
            except OSError: pass
            else:
                connection.close(); raise RuntimeError('SSH reachable over bridge')
            reports.append({'box': name, 'machine_id': h.guest(c, name, ['cat', '/etc/machine-id']).strip(),
                            'ssh_key': h.guest(c, name, ['ssh-keygen', '-lf', '/srv/chart/data/identity/ssh/ssh_host_ed25519_key.pub']).split()[1],
                            'tailscale_id': ts['Self']['ID'], 'ip': ip})
        for key in ('machine_id', 'ssh_key', 'tailscale_id', 'ip'):
            p.require(reports[0][key] != reports[1][key], 'Duplicated identity: ' + key)
        # No automatic default-image alias: operators create from the recorded verified fingerprint.
        record.update(phase='verified', verified=True, acceptance=reports)
        h.save(directory / 'build.json', record)
        print(json.dumps({'verified': record['fingerprint'], 'boxes_retained': record['tests']}, indent=2))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    f = sub.add_parser('fetch'); f.add_argument('--nvm-artifacts', required=True)
    build_parser = sub.add_parser('image-build')
    for name in ('config', 'build', 'artifacts', 'nvm-artifacts', 'ssh-key'): build_parser.add_argument('--' + name, required=True)
    build_parser.add_argument('--apply', action='store_true')
    for command in ('image-resume', 'image-verify'):
        v = sub.add_parser(command)
        for name in ('config', 'build'): v.add_argument('--' + name, required=True)
        v.add_argument('--apply', action='store_true')
    a = parser.parse_args()
    if a.command == 'fetch': artifacts(a.nvm_artifacts, fetch=True); print('Pinned nvm files verified.')
    elif a.command == 'image-build': build(a)
    elif a.command == 'image-resume': resume(a)
    else: verify(a)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
