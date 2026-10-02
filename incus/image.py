#!/usr/bin/env python3
"""Prepare explicit image inputs, build a private candidate, and gate alias promotion."""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time

import prep as p
sys.path.insert(0, str(p.HERE.parent))
import sync_common

OWNER = 'chart-incus-seed-v1'
RUNTIME = 'sha256:6bece393b989747b6787a66e1e75143a62fac289b41fa7abdd4ca6f9b76eb369'
HELPERS = ('box.py', 'box_config.py', 'backing.py', 'backing_checks.py', 'app_checks.py',
           'box_sync.py', 'sync_policy.py', 'laptop_sync.py', 'image_guest.py')


def save(path, value):
    path = Path(path)
    p.no_symlinks(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = path.with_suffix('.next')
    p.require(not tmp.exists(), 'Interrupted metadata write; inspect ' + str(tmp))
    with tmp.open('x') as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(value, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(path)


def pins(path):
    data = json.loads(Path(path).read_text())
    p.require(data.get('schema') == 1 and set(data['repos']) == set(sync_common.REPOS.values()), 'Expected exactly three backend pins')
    for repo, pin in data['repos'].items():
        p.require(re.fullmatch(r'[a-f0-9]{40}', pin['sha']), 'Pin a full commit SHA')
        p.require(re.fullmatch(r'git@gitlab.com:openmarketxyz/[a-z-]+/' + re.escape(repo) + r'\.git', pin['url']), 'Unexpected source URL')
    dt.date.fromisoformat(data['selected'])
    return data


def filter_archive(raw, destination):
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw)) as src, tarfile.open(destination, 'x') as out:
        for entry in src:
            rel = PurePosixPath(entry.name)
            p.require(not rel.is_absolute() and '..' not in rel.parts, 'Unsafe source archive')
            if sync_common.ignored(rel.parts): continue
            p.require(entry.isfile() or entry.isdir(), 'Included source link/device refused')
            key = rel.as_posix().casefold()
            p.require(key not in seen, 'Duplicate/case-colliding source archive path')
            seen.add(key)
            entry.uid = entry.gid = 0; entry.uname = entry.gname = ''
            entry.mode = 0o755 if entry.isdir() or entry.mode & 0o111 else 0o644
            out.addfile(entry, src.extractfile(entry) if entry.isfile() else None)


def prepare_inputs(a):
    lock = pins(a.sources_lock)
    out = Path(a.inputs).expanduser().absolute()
    cache = Path(a.git_cache).expanduser().absolute()
    p.no_symlinks(out); p.no_symlinks(cache)
    if not a.apply:
        print(json.dumps({'plan': 'fetch pinned Git objects, filter archives, save loaded runtime image',
                          'pins': lock, 'output': str(out)}, indent=2)); return
    p.require(os.geteuid() != 0, 'Prepare source inputs as the authenticated developer, not sudo/root')
    p.require(not out.exists(), 'Input generation exists; retain it and choose a new directory')
    out.mkdir(parents=True, mode=0o700); cache.mkdir(parents=True, mode=0o700, exist_ok=True)
    sources = {}
    for repo, pin in lock['repos'].items():
        bare = cache / (repo + '.git')
        p.no_symlinks(bare)
        if not bare.exists():
            p.run(['git', 'init', '--bare', bare])
            p.run(['git', '-C', bare, 'remote', 'add', 'origin', pin['url']])
        p.require(p.run(['git', '-C', bare, 'remote', 'get-url', 'origin']).strip() == pin['url'], 'Git cache origin differs')
        p.run(['git', '-C', bare, 'fetch', '--no-tags', 'origin', pin['sha']])
        raw = subprocess.check_output(['git', '-C', str(bare), 'archive', pin['sha']])
        filename = repo + '.tar'
        filter_archive(raw, out / filename)
        sources[repo] = {'archive': filename, 'revision': pin['sha'], 'sha256': p.digest(out / filename)}
    image = json.loads(p.run(['docker', 'image', 'inspect', a.runtime_image]))[0]
    p.require(image['Id'] == a.runtime_image and image['Architecture'] == 'amd64'
              and image['Config']['User'] == '1000:1000', 'Expected loaded immutable amd64 UID-1000 runtime')
    p.run(['docker', 'image', 'save', '--output', out / 'runtime.tar', a.runtime_image])
    save(out / 'inputs.json', {'schema': 1, 'pins': lock, 'sources': sources,
                             'runtime': {'archive': 'runtime.tar', 'image': a.runtime_image,
                                         'sha256': p.digest(out / 'runtime.tar')}})
    print('Prepared immutable input generation: ' + str(out))


def inputs(path):
    root = Path(path).expanduser().absolute(); p.no_symlinks(root)
    data = json.loads((root / 'inputs.json').read_text())
    p.require(data['schema'] == 1 and set(data['sources']) == set(sync_common.REPOS.values()), 'Incomplete image inputs')
    p.require(re.fullmatch(r'sha256:[a-f0-9]{64}', data['runtime']['image']), 'Invalid runtime image ID')
    for repo, entry in data['sources'].items():
        p.require(entry['revision'] == data['pins']['repos'][repo]['sha'], 'Source pin mismatch')
    for entry in [*data['sources'].values(), data['runtime']]:
        p.require(Path(entry['archive']).name == entry['archive'], 'Artifact name must be a basename')
        artifact = root / entry['archive']; p.no_symlinks(artifact)
        p.require(p.digest(artifact) == entry['sha256'], 'Artifact checksum differs: ' + entry['archive'])
    return root, data


def names(build):
    p.require(re.fullmatch(r'[a-z][a-z0-9-]{0,19}', build), 'Use a lowercase build name up to 20 characters')
    return 'chart-bld-' + build, ['chart-accept-' + build + '-' + s for s in ('a', 'b')]


def registry_line(path):
    path = Path(path).expanduser().absolute(); p.no_symlinks(path)
    lines = [line.strip() for line in path.read_text().splitlines()
             if line.strip().startswith('//registry.npmjs.org/:_authToken=')]
    p.require(len(lines) == 1 and lines[0].split('=', 1)[1].strip() and '${' not in lines[0],
              'Supply one resolved npmjs token entry; other registry entries are not forwarded')
    return lines[0] + '\n'


def guest(c, name, argv, stdin=None):
    return p.run(['incus', 'exec', 'local:' + name, '--project', c['project'], '--', *argv], input=stdin)


def push(c, name, source, destination):
    p.run(['incus', 'file', 'push', source, 'local:' + name + destination, '--project', c['project']])


def check_owned(c, record, name, fingerprint):
    obj = p.query(f'/1.0/instances/{name}?project={c["project"]}')
    wanted = p.instance_spec(c, name)
    p.require(obj['profiles'] == [] and obj['devices'] == wanted['devices'], 'Acceptance box devices/profiles differ')
    for key, value in wanted['config'].items():
        p.require(obj['config'].get(key) == value, 'Acceptance box config differs: ' + key)
    p.require(obj['config'].get('volatile.base_image') == fingerprint and
              not any(k.startswith(('raw.', 'limits.')) for k in obj['config']), 'Unexpected acceptance image/limits')
    marker = Path(c['boxes_root']) / name / '.chart-incus-box.json'
    p.require(json.loads(marker.read_text()) == record['box_markers'][name], 'Acceptance HDD marker differs')
    return obj


def helper_snapshot(directory, commit):
    repository = p.HERE.parent
    paths = ['incus/' + n for n in (*HELPERS, 'guest-prepare.sh', 'image-base.sh', 'image_accept.py', 'versions.lock.json')]
    paths += ['sync_common.py', 'runtime/launch.cjs']
    for relative in paths:
        text = p.run(['git', '-c', 'safe.directory=' + str(repository), '-C', repository, 'show', commit + ':' + relative])
        p.write_owned(directory / 'code' / relative, text, mode=0o644)
    return directory / 'code'


def new_acceptance(c, record, name, key, directory, code):
    spec = p.instance_spec(c, name); spec['source']['fingerprint'] = record['fingerprint']
    target = Path(c['boxes_root']) / name
    p.no_symlinks(target)
    p.require(not target.exists(), 'Retained acceptance data exists; use a distinct build name')
    target.mkdir(mode=0o700)
    marker = {'owner': p.OWNER, 'name': name, 'machine_id': c['machine_id'], 'hdd_uuid': c['hdd_uuid']}
    p.write_owned(target / '.chart-incus-box.json', json.dumps(marker, indent=2) + '\n')
    record['box_markers'][name] = marker
    save(directory / 'build.json', record)
    p.query('/1.0/instances?project=' + c['project'], spec, 'POST')
    check_owned(c, record, name, record['fingerprint'])
    p.run(['incus', 'start', 'local:' + name, '--project', c['project']])
    guest(c, name, ['install', '-d', '-m', '700', '/root/chart-prep'])
    push(c, name, code / 'incus/guest-prepare.sh', '/root/chart-prep/guest-prepare.sh')
    push(c, name, key, '/root/chart-prep/authorized_key')
    timezone = p.run(['timedatectl', 'show', '--property=Timezone', '--value']).strip()
    packages = [n + '=' + p.PINS['packages'][n] for n in ('docker.io', 'docker-compose-v2', 'openssh-server', 'nftables', 'iptables')]
    output = guest(c, name, ['env', 'CHART_FROM_IMAGE=1', 'bash', '/root/chart-prep/guest-prepare.sh', timezone, *packages])
    p.write_owned(directory / (name + '-provision.log'), output)


def build(a):
    c = p.config(a.config); p.check_host(c)
    root, data = inputs(a.inputs)
    builder_name, acceptance = names(a.build)
    key = p.public_key(a.ssh_key)
    report = {'builder': builder_name, 'acceptance_boxes': acceptance, 'base': p.PINS['image']['fingerprint'],
              'pins': data['pins'], 'runtime': data['runtime']['image'], 'promote_after_acceptance': a.promote,
              'stages': ['clean build', 'private unaliased candidate', 'two independent HDD acceptance boxes',
                         'operator Tailscale enrollment', 'image-verify gates', 'optional alias promotion']}
    print(json.dumps(report, indent=2), flush=True)
    age = (dt.date.today() - dt.date.fromisoformat(data['pins']['selected'])).days
    if age > 7: print(f'Pinned selection is {age} days old; no automatic ref update.', flush=True)
    if not a.apply: return
    p.root()
    credential = registry_line(a.npmrc)
    with p.locked():
        p.require(json.loads(p.HOST_CONFIG.read_text()) == c, 'Installed host config differs')
        for kind, key in [('projects', 'project'), ('networks', 'bridge'), ('storage-pools', 'pool')]:
            obj = p.query('/1.0/' + kind + '/' + c[key])
            p.require(obj['config'].get('user.chart-infra') == p.OWNER, 'Foreign Incus resource: ' + kind)
        p.require(not p.capacity(c)['review'], 'Review SSD capacity before building')
        git = ['git', '-c', 'safe.directory=' + str(p.HERE.parent), '-C', p.HERE.parent]
        p.require(not p.run([*git, 'status', '--porcelain']).strip(), 'Build only a clean committed helper revision')
        commit = p.run([*git, 'rev-parse', 'HEAD']).strip()
        directory = p.STATE / 'images' / a.build
        p.require(not directory.exists(), 'Build generation exists; inspect retained state and use a new name for retry')
        available = p.query('/1.0/instances?project=' + c['project'] + '&recursion=1')
        p.require(not any(i['name'] in [builder_name, *acceptance] for i in available), 'Instance name collision')
        directory.mkdir(parents=True, mode=0o700)
        code = helper_snapshot(directory, commit)
        artifacts = p.artifacts(a.artifacts)
        input_copy = directory / 'inputs'
        input_copy.mkdir(mode=0o700)
        for entry in [*data['sources'].values(), data['runtime']]:
            shutil.copyfile(root / entry['archive'], input_copy / entry['archive'])
            p.require(p.digest(input_copy / entry['archive']) == entry['sha256'], 'Inputs changed while taking build snapshot')
        save(input_copy / 'inputs.json', data)
        p.write_owned(directory / 'authorized_key', key)
        record = {**report, 'owner': OWNER, 'build': a.build, 'commit': commit, 'phase': 'building', 'box_markers': {}}
        save(directory / 'build.json', record)
        images = p.query('/1.0/images?project=' + c['project'] + '&recursion=1')
        if not any(i['fingerprint'] == record['base'] for i in images):
            p.run(['incus', 'image', 'import', artifacts / 'incus.tar.xz', artifacts / 'rootfs.tar.xz',
                   'local:', '--project', c['project']])
        spec = p.instance_spec(c, builder_name)
        del spec['devices']['data']; del spec['devices']['tun']
        p.query('/1.0/instances?project=' + c['project'], spec, 'POST')
        p.run(['incus', 'start', 'local:' + builder_name, '--project', c['project']])
        guest(c, builder_name, ['timeout', '90', 'sh', '-c', 'until ip -4 route | grep -q default; do sleep 1; done'])
        guest(c, builder_name, ['install', '-d', '-m', '700', '/root/chart-image'])
        marker = {'owner': OWNER, 'build': a.build, 'name': builder_name, 'commit': commit}
        guest(c, builder_name, ['python3', '-c', 'import sys;from pathlib import Path;Path("/var/lib/chart-image-builder").write_text(sys.stdin.read())'], json.dumps(marker))
        push(c, builder_name, artifacts / 'tailscale.deb', '/root/chart-image/tailscale.deb')
        push(c, builder_name, code / 'incus/image-base.sh', '/root/chart-image/image-base.sh')
        packages = [n + '=' + p.PINS['packages'][n] for n in ('docker.io', 'docker-compose-v2', 'openssh-server', 'nftables', 'iptables')]
        print('Preparing pinned guest packages in ' + builder_name, flush=True)
        p.write_owned(directory / 'packages.log', guest(c, builder_name, ['env', 'DEBIAN_FRONTEND=noninteractive', 'bash', '/root/chart-image/image-base.sh', *packages]))
        for filename in HELPERS:
            push(c, builder_name, code / 'incus' / filename, '/opt/chart-infra/incus/' + filename)
        for filename, source in [('sync_common.py', code / 'sync_common.py'), ('launch.cjs', code / 'runtime/launch.cjs')]:
            push(c, builder_name, source, '/opt/chart-infra/incus/' + filename)
        for filename in ['inputs.json', *(r['archive'] for r in data['sources'].values()), data['runtime']['archive']]:
            push(c, builder_name, input_copy / filename, '/root/chart-image/' + filename)
        guest(c, builder_name, ['docker', 'image', 'load', '--input', '/root/chart-image/' + data['runtime']['archive']])
        import backing
        for image in backing.IMAGES.values(): guest(c, builder_name, ['docker', 'pull', image])
        print('Preparing portable source/dependencies; private logs stay under ' + str(directory), flush=True)
        p.write_owned(directory / 'dependencies.log', guest(c, builder_name, ['python3', '/opt/chart-infra/incus/image_guest.py', 'prepare'], credential))
        p.run(['incus', 'file', 'pull', 'local:' + builder_name + '/var/lib/chart-seed/manifest.json', directory / 'seed-manifest.json', '--project', c['project']])
        p.run(['incus', 'file', 'pull', 'local:' + builder_name + '/var/lib/chart-image-packages.tsv', directory / 'packages.tsv', '--project', c['project']])
        p.write_owned(directory / 'seal.log', guest(c, builder_name, ['python3', '/opt/chart-infra/incus/image_guest.py', 'seal']))
        p.run(['incus', 'stop', 'local:' + builder_name, '--project', c['project']])
        print('Publishing private candidate; no default alias moves.', flush=True)
        output = p.run(['incus', 'publish', 'local:' + builder_name, 'local:', '--project', c['project'],
                        'chart.owner=' + OWNER, 'chart.build=' + a.build, 'chart.commit=' + commit,
                        'chart.pins=' + json.dumps({r: v['sha'] for r, v in data['pins']['repos'].items()}, sort_keys=True)])
        p.write_owned(directory / 'publish.log', output)
        images = p.query('/1.0/images?project=' + c['project'] + '&recursion=1')
        matches = [i for i in images if i.get('properties', {}).get('chart.build') == a.build and i.get('properties', {}).get('chart.owner') == OWNER]
        p.require(len(matches) == 1 and not matches[0]['public'], 'Cannot identify unique private candidate')
        record.update(fingerprint=matches[0]['fingerprint'], phase='acceptance-pending')
        save(directory / 'build.json', record)
        for name in acceptance:
            p.require(not p.capacity(c)['review'], 'Review capacity before acceptance box creation')
            print('Preparing independent acceptance box ' + name, flush=True)
            new_acceptance(c, record, name, directory / 'authorized_key', directory, code)
        print('Candidate retained. Enroll both acceptance boxes, then run image-verify. No alias moved.', flush=True)
        for name in acceptance:
            print(f'sudo incus exec local:{name} --project {c["project"]} -- tailscale up --hostname={name} --accept-routes=false --accept-dns=true --ssh=false')


def alias_update(c, name, fingerprint):
    aliases = p.query('/1.0/images/aliases?project=' + c['project'] + '&recursion=1')
    existing = next((x for x in aliases if x['name'] == name), None)
    value = {'description': OWNER, 'target': fingerprint}
    if existing:
        p.require(existing['description'] == OWNER, 'Foreign alias: ' + name)
        p.query('/1.0/images/aliases/' + name + '?project=' + c['project'], value, 'PUT')
    else:
        p.query('/1.0/images/aliases?project=' + c['project'], {'name': name, **value}, 'POST')


def exposure_checks(c, reports):
    addresses = {}
    for report in reports:
        name = report['box']
        rows = json.loads(guest(c, name, ['ip', '-j', '-4', 'addr', 'show', 'eth0']))
        addresses[name] = next(a['local'] for row in rows for a in row['addr_info'] if a['scope'] == 'global')
        for port in (22, 27017, 3000):
            with socket.create_connection((report['tailscale_ip'], port), timeout=3): pass
            try:
                connection = socket.create_connection((addresses[name], port), timeout=2)
            except OSError:
                continue
            connection.close()
            raise RuntimeError(f'Bridge exposure detected: {name}:{port}')
    for report in reports:
        peer = next(r for r in reports if r['box'] != report['box'])
        script = ('import socket,sys\ntry:\n s=socket.create_connection((' + repr(addresses[peer['box']]) +
                  ',22),timeout=3);s.close()\nexcept OSError: sys.exit(0)\nsys.exit(1)\n')
        guest(c, report['box'], ['python3', '-c', script])


def verify(a):
    c = p.config(a.config); p.check_host(c); names(a.build)
    if not a.apply:
        print('Plan: verify both enrolled acceptance boxes, prepare fresh fixtures, prove retention and identity independence; promote only if requested by image-build.'); return
    with p.locked():
        directory = p.STATE / 'images' / a.build
        record = json.loads((directory / 'build.json').read_text())
        p.require(record['owner'] == OWNER and record['phase'] in ('acceptance-pending', 'verified'), 'Build is not ready for verification')
        fingerprint = record['fingerprint']
        image = p.query('/1.0/images/' + fingerprint + '?project=' + c['project'])
        p.require(not image['public'] and image['properties'].get('chart.build') == a.build, 'Candidate identity differs')
        identities = []
        for name in record['acceptance_boxes']:
            check_owned(c, record, name, fingerprint)
            ip = guest(c, name, ['tailscale', 'ip', '-4']).strip()
            import ipaddress
            p.require(ipaddress.ip_address(ip) in ipaddress.ip_network('100.64.0.0/10'), 'Enroll acceptance box in Tailscale first')
        for name in record['acceptance_boxes']:
            print('Running fixture, egress and retention gates in ' + name, flush=True)
            push(c, name, directory / 'code/incus/image_accept.py', '/root/chart-prep/image_accept.py')
            output = guest(c, name, ['python3', '/root/chart-prep/image_accept.py', '--box', name])
            p.write_owned(directory / (name + '-accept-' + str(time.time_ns()) + '.log'), output)
            report = json.loads(guest(c, name, ['cat', '/srv/chart/data/identity/image-accept.json']))
            p.require(report['passed'], 'Acceptance failed')
            identities.append(report)
        for field in ('machine_id', 'ssh_key', 'tailscale_id', 'mongo_identity', 'database_credentials',
                      'app_identity', 'tailscale_ip', 'signing_auth', 'signing_tharamine', 'signing_orange', 'signing_backend'):
            p.require(identities[0][field] != identities[1][field], 'Cloned identity: ' + field)
        exposure_checks(c, identities)
        for report in identities:
            for script in ('box.py', 'backing.py'):
                guest(c, report['box'], ['python3', '/opt/chart-infra/incus/' + script, 'stop', '--box', report['box'], '--apply'])
        record['phase'] = 'verified'; record['acceptance'] = identities
        save(directory / 'build.json', record)
        if record['promote_after_acceptance']:
            aliases = p.query('/1.0/images/aliases?project=' + c['project'] + '&recursion=1')
            current = next((x for x in aliases if x['name'] == 'chart-golden'), None)
            if current:
                p.require(current['description'] == OWNER, 'Foreign golden alias')
                if current['target'] != fingerprint: alias_update(c, 'chart-golden-previous', current['target'])
            alias_update(c, 'chart-golden', fingerprint)
            record['promoted'] = True; save(directory / 'build.json', record)
        print(json.dumps({'verified': fingerprint, 'promoted': record.get('promoted', False), 'retained': record['acceptance_boxes']}, indent=2))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    fetch = sub.add_parser('prepare-inputs')
    fetch.add_argument('--sources-lock', default=str(p.HERE / 'sources.lock.json'))
    fetch.add_argument('--git-cache', required=True); fetch.add_argument('--inputs', required=True)
    fetch.add_argument('--runtime-image', default=RUNTIME); fetch.add_argument('--apply', action='store_true')
    build_parser = sub.add_parser('image-build')
    build_parser.add_argument('--config', required=True); build_parser.add_argument('--build', required=True)
    build_parser.add_argument('--inputs', required=True); build_parser.add_argument('--artifacts', required=True)
    build_parser.add_argument('--npmrc', required=True); build_parser.add_argument('--ssh-key', required=True)
    build_parser.add_argument('--promote', action='store_true'); build_parser.add_argument('--apply', action='store_true')
    verify_parser = sub.add_parser('image-verify')
    verify_parser.add_argument('--config', required=True); verify_parser.add_argument('--build', required=True)
    verify_parser.add_argument('--apply', action='store_true')
    a = parser.parse_args()
    if a.action == 'prepare-inputs': prepare_inputs(a)
    elif a.action == 'image-build': build(a)
    else: verify(a)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
