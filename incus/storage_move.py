#!/usr/bin/env python3
"""Verify cross-pool moves before relocating personal-box root filesystems."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import prep as p
import host_common as h
import storage_trial as t
import syslog_fix

OWNER = 'chart-storage-move-v1'
RESET_KEYS = ('volatile.idmap.base', 'volatile.idmap.next',
              'volatile.cloud-init.instance-id', 'volatile.apply_template')
RUNTIME_KEYS = ('volatile.eth0.host_name', 'volatile.last_state.power', 'volatile.last_state.ready')
FIXTURE = r'''
import hashlib,json,os,pathlib,sys
paths=[pathlib.Path('/root/chart-move-proof'),pathlib.Path('/srv/chart/data/chart-move-proof')]
if sys.argv[1]=='create':
    for root in paths:
        root.mkdir()
        for name,uid in [('root',0),('developer',1000)]:
            file=root/name; file.write_bytes(b'chart-move-fixture-v1\n'*1024)
            file.chmod(0o640); os.chown(file,uid,uid)
result={}
for root in paths:
    for file in sorted(root.iterdir()):
        st=file.stat()
        result[str(file)]={'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),
                           'uid':st.st_uid,'gid':st.st_gid,'mode':st.st_mode & 0o7777}
result['uid_map']=pathlib.Path('/proc/1/uid_map').read_text()
result['gid_map']=pathlib.Path('/proc/1/gid_map').read_text()
print(json.dumps(result))
'''


def guest(c, name, args):
    return p.run(['timeout', '--foreground', '--kill-after=5', '180', 'incus', 'exec', 'local:' + name,
                  '--project', c['project'], '--disable-stdin', '--force-noninteractive', '--', *args], input='')


def hdd_capacity(c, needed=0):
    t.validate_pool(c, p.query('/1.0/storage-pools/hdd'))
    disk = shutil.disk_usage(c['hdd_mount'])
    p.require(disk.free - needed > max(10 * 1024**3, disk.total * .15), 'Keep 15% HDD free and at least 10 GiB')


def validate_move(before, after, pool):
    devices = copy.deepcopy(before['devices']); devices['root']['pool'] = pool
    p.require(after['devices'] == devices and after['profiles'] == before['profiles'],
              'Move changed devices other than root pool, or profiles')
    for key in ('name', 'type', 'architecture', 'ephemeral', 'description'):
        p.require(after.get(key) == before.get(key), 'Move changed ' + key)
    def stable(config):
        return {key: value for key, value in config.items() if key not in RUNTIME_KEYS and value != ''}
    old, new = stable(before['config']), stable(after['config'])
    changed = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
    p.require(not changed, 'Move changed identity, UID map or configuration: ' + ', '.join(changed))


def original_range_free(c, before):
    config = before['config']
    p.require(config.get('security.idmap.isolated') == 'true' and not config.get('raw.idmap'),
              'Require an isolated map without raw overrides')
    base = int(config['volatile.idmap.base']); size = int(config['security.idmap.size'])
    expected = [{'Isuid': uid, 'Isgid': not uid, 'Hostid': base, 'Nsid': 0, 'Maprange': size}
                for uid in (True, False)]
    p.require(base > 0 and size == 65536, 'Unexpected original UID range')
    for key in ('volatile.idmap.current', 'volatile.idmap.next'):
        p.require(json.loads(config[key]) == expected, 'Original current/next maps disagree')
    for obj in p.query('/1.0/instances?all-projects=true&recursion=1'):
        if obj.get('project', 'default') == c['project'] and obj['name'] == before['name']: continue
        if obj.get('type') != 'container': continue
        expanded = obj['expanded_config']
        for key in ('volatile.idmap.current', 'volatile.idmap.next', 'volatile.last_state.idmap'):
            for entry in json.loads(expanded.get(key) or '[]'):
                start, count = entry['Hostid'], entry['Maprange']
                p.require(count > 0 and (start + count <= base or base + size <= start),
                          'Original UID/GID range overlaps another instance: ' + obj['name'])


def restore_move_metadata(c, name, before, pool, evidence):
    after = h.instance(c, name)
    p.require(after['status'] == 'Stopped', 'Metadata repair requires a stopped instance')
    p.require(not before['config'].get('volatile.apply_template'), 'Source has pending templates')
    p.require(after['config'].get('volatile.apply_template', '') in ('', 'copy'), 'Unexpected template trigger')
    candidate = copy.deepcopy(after)
    for key in RESET_KEYS:
        candidate['config'][key] = before['config'].get(key, '')
    validate_move(before, candidate, pool)
    original_range_free(c, before)
    patch = {key: before['config'].get(key, '') for key in RESET_KEYS
             if after['config'].get(key, '') != before['config'].get(key, '')}
    p.no_symlinks(evidence)
    p.require(not evidence.exists(), 'Metadata repair receipt exists; inspect before retry')
    record = {'owner': OWNER, 'host': c['machine_id'], 'box': name, 'pool': pool,
              'phase': 'prepared', 'before_repair': after, 'patch': patch}
    h.save(evidence, record)
    if patch:
        p.query(f'/1.0/instances/{name}?project={c["project"]}',
                {'config': patch, 'description': after.get('description', '')}, 'PATCH')
    repaired = h.instance(c, name)
    p.require(repaired['status'] == 'Stopped', 'Instance started during metadata repair')
    validate_move(before, repaired, pool)
    record.update(phase='verified', after_repair=repaired); h.save(evidence, record)
    return repaired


def transfer(c, name, before, pool, evidence=None):
    current = h.instance(c, name)
    validate_move(before, current, current['devices']['root']['pool'])
    p.require(current['status'] == 'Stopped', 'Cross-pool move requires a stopped instance')
    p.run(['incus', 'move', 'local:' + name, '--storage', pool, '--project', c['project']], input='')
    after = h.instance(c, name)
    p.require(after['status'] == 'Stopped', 'Moved instance unexpectedly running')
    if evidence is not None:
        after = restore_move_metadata(c, name, before, pool, evidence)
    else:
        validate_move(before, after, pool)
    return after


def verify(c, trial, fingerprint):
    h.host(c); h.candidate(c, fingerprint, verified=True); hdd_capacity(c)
    t.trial_names(trial)
    name = 'chart-move-' + trial; p.box_name(name)
    directory = p.STATE / 'storage-moves' / trial
    data = Path(c['hdd_mount']) / 'shared-dev/storage-fixtures' / trial
    p.no_symlinks(directory); p.no_symlinks(data)
    p.require(not directory.exists() and not data.exists(), 'Retained move proof/data exists; inspect before retry')
    p.require(not any(i['name'] == name for i in p.query('/1.0/instances?project=' + c['project'] + '&recursion=1')),
              'Move fixture name collision')
    p.require(not p.capacity(c)['review'], 'Review SSD capacity before fixture creation')
    record = {'owner': OWNER, 'host': c['machine_id'], 'trial': trial, 'image': fingerprint,
              'fixture': name, 'data': str(data), 'phase': 'creating', 'moves': []}
    proof = directory / 'verify.json'; h.save(proof, record)
    data.mkdir(parents=True, mode=0o700)
    h.save(data / '.chart-storage-fixture.json', {'owner': OWNER, 'trial': trial, 'host': c['machine_id']})
    spec = t.fixture_spec(c, name, c['pool'], fingerprint, trial)
    spec['devices']['data'] = {'type': 'disk', 'source': str(data), 'path': '/srv/chart/data',
                               'required': 'true', 'shift': 'true'}
    p.query('/1.0/instances?project=' + c['project'], spec, 'POST')
    try:
        check = lambda: t.validate_fixture(c, name, spec)
        check()
        p.run(['incus', 'start', 'local:' + name, '--project', c['project']], input='')
        h.wait_ready(c, name)
        syslog_fix.mitigate(c, name, check, directory / 'syslog.json')
        baseline = json.loads(guest(c, name, ['python3', '-c', FIXTURE, 'create']))
        record['baseline'] = baseline; h.save(proof, record)
        guest(c, name, ['bash', '-ec', t.SMOKE])
        before = h.instance(c, name); record['instance_before'] = before
        for pool in ('hdd', c['pool'], 'hdd'):
            record['phase'] = 'stopping-before-' + pool; h.save(proof, record)
            check()
            p.run(['incus', 'stop', 'local:' + name, '--project', c['project'], '--timeout', '120'], input='')
            record['phase'] = 'moving-to-' + pool; h.save(proof, record)
            transfer(c, name, before, pool, directory / f'move-{len(record["moves"])}-metadata.json')
            spec['devices']['root']['pool'] = pool
            print('Checking rootfs and idmapped HDD data on ' + pool, flush=True)
            p.run(['incus', 'start', 'local:' + name, '--project', c['project']], input='')
            h.wait_ready(c, name); check()
            p.require(json.loads(guest(c, name, ['python3', '-c', FIXTURE, 'read'])) == baseline,
                      'File contents, ownership, modes or UID/GID map changed')
            guest(c, name, ['sh', '-ec', 'systemctl start docker; docker start -a chart-storage-fixture'])
            p.require(syslog_fix.nonblocking(json.loads(guest(c, name, ['python3', '-c', syslog_fix.PROBE]))),
                      'Syslog descriptors are blocking')
            record['moves'].append({'pool': pool, 'files_and_uid_map': 'passed', 'docker': 'passed'})
            h.save(proof, record)
        record['phase'] = 'final-stop'; h.save(proof, record)
        p.run(['incus', 'stop', 'local:' + name, '--project', c['project'], '--timeout', '120'], input='')
        p.require(check()['status'] == 'Stopped', 'Fixture did not stop')
        record['phase'] = 'verified'; h.save(proof, record)
        print(json.dumps({'evidence': str(proof), 'moves': record['moves'],
                          'retained_stopped_fixture': name, 'retained_hdd': str(data)}, indent=2))
    except (Exception, KeyboardInterrupt) as error:
        record.update(failed_step=record['phase'], phase='failed', error=str(error))
        h.save(proof, record)
        raise


def resume_fixture(c, trial, fingerprint):
    h.host(c); h.candidate(c, fingerprint, verified=True); hdd_capacity(c)
    directory = p.STATE / 'storage-moves' / trial; p.no_symlinks(directory)
    original = directory / 'verify.json'
    record = json.loads(original.read_text())
    name = 'chart-move-' + trial
    data = Path(c['hdd_mount']) / 'shared-dev/storage-fixtures' / trial
    p.no_symlinks(data)
    p.require(record['owner'] == OWNER and record['host'] == c['machine_id']
              and record['trial'] == trial and record['fixture'] == name
              and record['image'] == fingerprint and record['data'] == str(data)
              and record['phase'] == 'failed' and record['failed_step'] == 'moving-to-hdd'
              and record['moves'] == [], 'Require the failed first HDD transfer receipt')
    p.require(json.loads((data / '.chart-storage-fixture.json').read_text()) ==
              {'owner': OWNER, 'trial': trial, 'host': c['machine_id']}, 'Synthetic HDD marker differs')
    p.require(data.stat().st_dev == Path(c['hdd_mount']).stat().st_dev, 'Synthetic data is not on HDD')
    proof = directory / 'resume.json'
    p.require(not proof.exists(), 'Resume receipt exists; inspect before retry')
    before = record['instance_before']
    spec = t.fixture_spec(c, name, 'hdd', fingerprint, trial)
    spec['devices']['data'] = {'type': 'disk', 'source': str(data), 'path': '/srv/chart/data',
                               'required': 'true', 'shift': 'true'}
    check = lambda: t.validate_fixture(c, name, spec)
    p.require(check()['status'] == 'Stopped', 'Failed fixture must still be stopped on HDD')
    record['original_failure'] = {'step': record.pop('failed_step'), 'error': record.pop('error', '')}
    record.update(original_receipt_sha256=p.digest(original), phase='restoring-first-move-metadata')
    h.save(proof, record)
    try:
        restore_move_metadata(c, name, before, 'hdd', directory / 'resume-0-metadata.json')
        for index, pool in enumerate(('hdd', c['pool'], 'hdd')):
            if index:
                record['phase'] = 'moving-to-' + pool; h.save(proof, record)
                transfer(c, name, before, pool, directory / f'resume-{index}-metadata.json')
                spec['devices']['root']['pool'] = pool
            record['phase'] = 'checking-on-' + pool; h.save(proof, record)
            print('Checking retained files, UID maps and Docker on ' + pool, flush=True)
            p.run(['incus', 'start', 'local:' + name, '--project', c['project']], input='')
            h.wait_ready(c, name); check(); validate_move(before, h.instance(c, name), pool)
            p.require(json.loads(guest(c, name, ['python3', '-c', FIXTURE, 'read'])) == record['baseline'],
                      'File contents, ownership, modes or UID/GID map changed')
            guest(c, name, ['sh', '-ec', 'systemctl start docker; docker start -a chart-storage-fixture'])
            p.require(syslog_fix.nonblocking(json.loads(guest(c, name, ['python3', '-c', syslog_fix.PROBE]))),
                      'Syslog descriptors are blocking')
            record['phase'] = 'stopping-on-' + pool; h.save(proof, record)
            p.run(['incus', 'stop', 'local:' + name, '--project', c['project'], '--timeout', '120'], input='')
            p.require(check()['status'] == 'Stopped', 'Fixture did not stop')
            record['moves'].append({'pool': pool, 'files_and_uid_map': 'passed', 'docker': 'passed'})
            h.save(proof, record)
        record['phase'] = 'verified'; h.save(proof, record)
        print(json.dumps({'evidence': str(proof), 'moves': record['moves'],
                          'retained_stopped_fixture': name, 'retained_hdd': str(data)}, indent=2))
    except (Exception, KeyboardInterrupt) as error:
        record.update(failed_step=record['phase'], phase='failed', error=str(error)); h.save(proof, record)
        raise


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'resume-fixture'])
    parser.add_argument('--config', required=True); parser.add_argument('--trial', required=True)
    parser.add_argument('--image', required=True); parser.add_argument('--apply', action='store_true')
    a = parser.parse_args(); c = p.config(a.config); p.check_host(c); t.trial_names(a.trial)
    p.require(len(a.image) == 64 and all(x in '0123456789abcdef' for x in a.image), 'Supply a full fingerprint')
    print(json.dumps({'command': a.command, 'fixture': 'chart-move-' + a.trial, 'moves': [c['pool'], 'hdd', c['pool'], 'hdd'],
                      'synthetic_hdd_attachment': True, 'existing_instances': 'untouched',
                      'retention': 'all fixture files and evidence retained', 'apply': a.apply}, indent=2), flush=True)
    if a.apply:
        with p.locked():
            if a.command == 'verify': verify(c, a.trial, a.image)
            else: resume_fixture(c, a.trial, a.image)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print('Refused:', error, file=sys.stderr); sys.exit(1)
