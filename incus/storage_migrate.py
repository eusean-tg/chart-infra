#!/usr/bin/env python3
"""Move a personal box to HDD with independent backups and explicit rollback."""
import argparse
import hashlib
import json
import os
import copy
import posixpath
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import prep as p
import host_common as h
import backup
import storage_move as m
import syslog_fix

TOOL_FILES = ('backup.py', 'host_common.py', 'prep.py', 'versions.lock.json', 'host.example.json')
TIMER = 'chart-box-backup.timer'
IDENTITY = r'''
import hashlib,json,pathlib,subprocess
files=['/etc/machine-id','/root/.ssh/authorized_keys',
       '/srv/chart/data/identity/ssh/ssh_host_ed25519_key',
       '/srv/chart/data/identity/ssh/ssh_host_rsa_key']
result={path:hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() for path in files}
ts=json.loads(subprocess.check_output(['tailscale','status','--json'],text=True))
assert ts.get('BackendState')=='Running', 'Tailscale not ready'
result['tailscale']={'id':ts['Self']['ID'],'ips':sorted(ts['Self']['TailscaleIPs'])}
result['uid_map']=pathlib.Path('/proc/1/uid_map').read_text()
result['gid_map']=pathlib.Path('/proc/1/gid_map').read_text()
print(json.dumps(result))
'''


def rootfs_manifest(path):
    prefix = 'backup/container/rootfs/'
    entries = {}
    apparent = 0
    with tarfile.open(path, 'r|*') as archive:
        for entry in archive:
            name = entry.name.removeprefix('./')
            if not name.startswith(prefix): continue
            relative = name[len(prefix):].rstrip('/')
            if not relative and entry.isdir(): continue
            p.require(relative and '..' not in Path(relative).parts, 'Unexpected rootfs archive path')
            p.require(relative not in entries, 'Duplicate rootfs archive entry')
            item = {'mode': entry.mode, 'uid': entry.uid, 'gid': entry.gid,
                    'xattrs': {k:v for k,v in entry.pax_headers.items() if k.startswith(('SCHILY.xattr.', 'SCHILY.acl.'))}}
            if entry.isfile():
                stream = archive.extractfile(entry)
                p.require(stream is not None, 'Missing rootfs file content')
                with stream: item.update(kind='file', sha256=hashlib.file_digest(stream, 'sha256').hexdigest())
            elif entry.islnk():
                target = posixpath.normpath(entry.linkname.removeprefix('./'))
                p.require(target.startswith(prefix), 'Rootfs hard link points outside the rootfs')
                linked = entries.get(target[len(prefix):])
                p.require(linked is not None and linked['kind'] == 'file',
                          'Rootfs hard link must refer to a preceding regular file')
                item.update(kind='file', sha256=linked['sha256'])
            elif entry.issym(): item.update(kind='symlink', target=entry.linkname)
            elif entry.isdir(): item.update(kind='directory')
            else: item.update(kind=entry.type.decode('ascii'), major=entry.devmajor, minor=entry.devminor)
            entries[relative] = item
            apparent += (entry.size if entry.isfile() or entry.islnk() else 0) + 4096
            if len(entries) % 10000 == 0:
                print(f'Rootfs verification: {len(entries)} entries scanned', flush=True)
    p.require('etc/machine-id' in entries and len(entries) > 100, 'Expected a complete non-optimized rootfs export')
    payload = json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()
    return {'entries': len(entries), 'sha256': hashlib.sha256(payload).hexdigest(), 'apparent_bytes': apparent}


def replace(path, content, original=None):
    path = Path(path); p.no_symlinks(path)
    if original is not None:
        p.require(path.read_bytes() == original, 'File changed during cutover: ' + str(path))
    st = path.stat()
    temporary = path.with_name(path.name + '.chart-move-' + str(time.time_ns()))
    with temporary.open('xb') as f:
        os.fchmod(f.fileno(), st.st_mode & 0o7777)
        os.fchown(f.fileno(), st.st_uid, st.st_gid)
        f.write(content); f.flush(); os.fsync(f.fileno())
    temporary.replace(path)


def set_config(config_path, before, after):
    paths = list(dict.fromkeys([Path(config_path).absolute(), p.HOST_CONFIG]))
    for path in paths:
        p.require(json.loads(path.read_text()) == before, 'Host configuration changed: ' + str(path))
    for path in paths:
        replace(path, (json.dumps(after, indent=2) + '\n').encode())


def timer_active():
    return subprocess.run(['systemctl', 'is-active', '--quiet', TIMER], stdin=subprocess.DEVNULL).returncode == 0


def save_tool(directory):
    installed = p.STATE / 'backup-tool'
    hashes = {}
    for name in TOOL_FILES:
        file = installed / name; p.no_symlinks(file)
        st = file.stat()
        p.require(st.st_uid == 0 and not st.st_mode & 0o022, 'Backup-tool file has unsafe ownership/mode')
        p.write_owned(directory / 'backup-tool-before' / name, file.read_text())
        hashes[name] = {'before': p.digest(file), 'after': p.digest(p.HERE / name)}
    h.save(directory / 'backup-tool-snapshot.json', hashes)
    for name in TOOL_FILES:
        replace(installed / name, (p.HERE / name).read_bytes())


def restore_tool(directory):
    receipt = directory / 'backup-tool-snapshot.json'
    if not receipt.exists(): return
    hashes = json.loads(receipt.read_text())
    p.require(set(hashes) == set(TOOL_FILES), 'Incomplete backup-tool snapshot')
    for name in TOOL_FILES:
        saved, installed = directory / 'backup-tool-before' / name, p.STATE / 'backup-tool' / name
        p.no_symlinks(saved); p.no_symlinks(installed)
        p.require(p.digest(saved) == hashes[name]['before'], 'Saved backup tool changed')
        p.require(p.digest(installed) in hashes[name].values(), 'Installed backup tool changed outside migration')
    for name in TOOL_FILES:
        replace(p.STATE / 'backup-tool' / name, (directory / 'backup-tool-before' / name).read_bytes())


def verified_trial(c, trial, image):
    directory = p.STATE / 'storage-moves' / trial; p.no_symlinks(directory)
    original = directory / 'verify.json'; p.no_symlinks(original)
    proof = directory / 'resume.json' if (directory / 'resume.json').exists() else original
    p.no_symlinks(proof)
    check = json.loads(proof.read_text())
    if proof != original:
        p.require(check['original_receipt_sha256'] == p.digest(original), 'Original trial receipt changed')
    p.require(check['owner'] == m.OWNER and check['host'] == c['machine_id'] and check['trial'] == trial
              and check['phase'] == 'verified' and check['image'] == image
              and [x['pool'] for x in check['moves']] == ['hdd', c['pool'], 'hdd']
              and all(x.get('files_and_uid_map') == 'passed' and x.get('docker') == 'passed'
                      for x in check['moves']), 'Verified round-trip fixture required')
    return {'path': str(proof), 'sha256': p.digest(proof)}


def source_capacity(c, needed):
    report = p.capacity(c)
    p.require(not report['review'], 'Source SSD capacity needs review before migration/rollback')
    space = report['pool']
    p.require(space['total'] - space['used'] - needed > space['total'] * .15,
              'Insufficient original pool capacity for rollback')
    backup.storage(c, backup.backup_root(c), needed)


def wait_identity(c, box, expected):
    deadline = time.monotonic() + 45
    while True:
        try:
            identity = json.loads(m.guest(c, box, ['python3', '-c', IDENTITY])); break
        except RuntimeError:
            p.require(time.monotonic() < deadline, 'Identity did not become ready; inspect box')
            time.sleep(1)
    p.require(identity == expected, 'SSH/Tailscale/OS identity or UID/GID mapping changed')
    return identity


def check_installed(c, name):
    script = ('import sys; sys.path.insert(0,sys.argv[1]); import prep as p, host_common as h; '
              'c=p.config(sys.argv[2]); h.host(c); h.owned(c,sys.argv[3]); print("installed backup ownership passed")')
    return p.run([sys.executable, '-c', script, p.STATE / 'backup-tool', p.HOST_CONFIG, name], input='').strip()


def export_manifest(c, box, dest):
    backup.storage(c, dest)
    p.require(not dest.exists(), 'Export destination exists')
    backup.copy_command(c, ['incus', 'export', 'local:' + box, dest,
                           '--project', c['project'], '--instance-only'])
    return rootfs_manifest(dest)


def finish_move(c, config_path, record, directory, phase):
    box, migration = record['box'], record['migration']
    receipt = directory / 'move.json'
    before, new = record['instance_before'], record['after_config']
    exported = Path(record['backup'])
    phase('verifying-original-rootfs')
    record['rootfs_before'] = rootfs_manifest(exported / 'rootfs.tar.gz')
    source_capacity(c, int(record['rootfs_before']['apparent_bytes'] * 1.15))
    phase('verifying-data-restore')
    record['restore_check'] = backup.restore(exported, backup.scratch_root() / ('storage-' + migration), c)
    phase('moving-rootfs-to-hdd')
    m.hdd_capacity(c, int(record['rootfs_before']['apparent_bytes'] * 1.15))
    m.transfer(c, box, before, 'hdd', directory / 'move-metadata.json')
    phase('verifying-stopped-rootfs')
    record['rootfs_after'] = export_manifest(c, box, exported / 'rootfs-hdd.tar.gz')
    p.require(record['rootfs_after']['sha256'] == record['rootfs_before']['sha256'],
              'Rootfs content/ownership/modes/xattrs differ')
    phase('registering-hdd-placement')
    set_config(config_path, c, new)
    h.host(new); h.owned(new, box)
    record['installed_backup_check'] = check_installed(new, box)
    phase('verifying-backup-on-hdd')
    code = ('import sys; sys.path.insert(0,sys.argv[1]); import backup,prep as p,host_common as h; '
            'c=p.config(sys.argv[2]); h.host(c); print(backup.backup(c,sys.argv[3],resume=False))')
    migrated_backup = Path(p.run([sys.executable, '-c', code, p.STATE / 'backup-tool', p.HOST_CONFIG, box], input='').strip())
    record['backup_after'] = str(migrated_backup)
    record['restore_after'] = backup.restore(migrated_backup, backup.scratch_root() / ('storage-' + migration + '-after'), new)
    phase('starting-migrated-box')
    p.run(['incus', 'start', 'local:' + box, '--project', c['project']], input='')
    h.wait_ready(new, box)
    identity = wait_identity(new, box, record['identity_before'])
    m.validate_move(before, h.owned(new, box), 'hdd')
    record['identity_after'] = identity
    record['services_after'] = m.guest(new, box, ['systemctl', 'list-units', '--type=service', '--state=running', '--no-pager', '--no-legend'])
    if record['timer_was_active']: p.run(['systemctl', 'start', TIMER], input='')
    phase('moved-awaiting-application-and-laptop-acceptance')
    print(json.dumps({'box': box, 'pool': 'hdd', 'evidence': str(receipt), 'backup': str(exported),
                      'installed_backup_check': record['installed_backup_check'],
                      'next': 'Verify application health/performance and resume the existing laptop mapping'}, indent=2))


def move(c, config_path, box, trial, migration):
    h.host(c)
    before = h.owned(c, box)
    p.require(before['config'].get('user.chart-box') == h.OWNER and before['status'] == 'Running',
              'Select a running personal box')
    p.require(before['devices']['root']['pool'] == c['pool'] and c['pool'] != 'hdd', 'Expected source pool')
    p.require(not p.query(f'/1.0/instances/{box}/snapshots?project={c["project"]}&recursion=1'),
              'Snapshot-bearing boxes need a separately sized backup plan')
    proof = verified_trial(c, trial, before['config'].get('volatile.base_image'))
    m.hdd_capacity(c)
    p.require(syslog_fix.nonblocking(json.loads(m.guest(c, box, ['python3', '-c', syslog_fix.PROBE]))),
              'Mitigate syslog before stopping the box')
    directory = p.STATE / 'storage-migrations' / migration; p.no_symlinks(directory)
    p.require(not directory.exists(), 'Migration receipt exists; inspect or use rollback')
    config_path = Path(config_path).absolute()
    p.require(json.loads(config_path.read_text()) == c, 'Input config changed')
    new = {**c, 'instance_pools': {**c.get('instance_pools', {}), box: 'hdd'}}
    record = {'owner': m.OWNER, 'host': c['machine_id'], 'box': box, 'migration': migration,
              'trial': trial, 'config_path': str(config_path), 'before_config': c, 'after_config': new,
              'instance_before': before, 'trial_proof': proof, 'phase': 'preflight', 'timer_was_active': timer_active()}
    record['rollback_argv'] = ['sudo', sys.executable, str(Path(__file__).resolve()), 'rollback',
                               '--config', str(config_path), '--migration', migration, '--sync-paused', '--apply']
    receipt = directory / 'move.json'; h.save(receipt, record)
    def phase(value):
        record['phase'] = value; h.save(receipt, record); print(value, flush=True)
    try:
        record['identity_before'] = json.loads(m.guest(c, box, ['python3', '-c', IDENTITY]))
        record['services_before'] = m.guest(c, box, ['systemctl', 'list-units', '--type=service', '--state=running', '--no-pager', '--no-legend'])
        phase('pausing-backup-timer')
        if record['timer_was_active']: p.run(['systemctl', 'stop', TIMER], input='')
        phase('updating-backup-tool')
        save_tool(directory)
        check_installed(c, box)
        phase('stopped-independent-backup')
        exported = backup.backup(c, box, rootfs=True, resume=False)
        record['backup'] = str(exported); h.save(receipt, record)
        finish_move(c, config_path, record, directory, phase)
    except (Exception, KeyboardInterrupt) as error:
        record.update(failed_step=record['phase'], phase='failed', error=str(error))
        h.save(receipt, record)
        print('Migration retained for inspection. Backup timer may be stopped; use the recorded rollback command.', file=sys.stderr)
        print('Rollback argv: ' + json.dumps(record['rollback_argv']), file=sys.stderr)
        raise


def rollback(c, config_path, migration):
    directory = p.STATE / 'storage-migrations' / migration; p.no_symlinks(directory)
    receipt = directory / 'move.json'; record = json.loads(receipt.read_text())
    p.require(record['owner'] == m.OWNER and record['host'] == c['machine_id'], 'Foreign migration receipt')
    p.require(str(Path(config_path).absolute()) == record['config_path'], 'Use the recorded config path')
    old, new, box = record['before_config'], record['after_config'], record['box']
    p.require(c in (old, new), 'Configuration changed after migration; inspect before rollback')
    for path in (Path(config_path), p.HOST_CONFIG):
        p.require(json.loads(path.read_text()) in (old, new), 'Unknown/foreign host configuration')
    p.check_host(old)
    obj = h.instance(old, box); pool = obj['devices']['root']['pool']
    p.require(pool in (old['pool'], 'hdd'), 'Unexpected root pool')
    p.require(record.get('identity_before'), 'Migration has no recorded identity; inspect before rollback')
    # Validate resettable copy metadata separately while preserving all other identity checks.
    candidate = copy.deepcopy(obj)
    if obj['status'] == 'Stopped':
        for key in m.RESET_KEYS: candidate['config'][key] = record['instance_before']['config'].get(key, '')
    m.validate_move(record['instance_before'], candidate, pool)
    if pool == 'hdd':
        p.require(record.get('rootfs_before'), 'Missing original rootfs sizing')
        source_capacity(old, int(record['rootfs_before']['apparent_bytes'] * 1.15))
    record['phase'] = 'rolling-back'; h.save(receipt, record)
    if timer_active(): p.run(['systemctl', 'stop', TIMER], input='')
    if obj['status'] == 'Running':
        p.run(['incus', 'stop', 'local:' + box, '--project', old['project'], '--timeout', '120'], input='')
    p.require(h.instance(old, box)['status'] == 'Stopped', 'Rollback requires a stopped box')
    stamp = str(time.time_ns())
    m.restore_move_metadata(old, box, record['instance_before'], pool,
                            directory / ('rollback-before-' + stamp + '.json'))
    if pool == 'hdd':
        m.transfer(old, box, record['instance_before'], old['pool'], directory / ('rollback-move-' + stamp + '.json'))
    # A partial config update can leave one old and one new copy. Restore only known values.
    for path in dict.fromkeys([Path(config_path).absolute(), p.HOST_CONFIG]):
        p.require(json.loads(path.read_text()) in (old, new), 'Host config changed during rollback')
        replace(path, (json.dumps(old, indent=2) + '\n').encode())
    h.host(old); h.owned(old, box)
    restore_tool(directory)
    check_installed(old, box)
    p.run(['incus', 'start', 'local:' + box, '--project', old['project']], input='')
    h.wait_ready(old, box)
    record['rollback_identity'] = wait_identity(old, box, record['identity_before'])
    m.validate_move(record['instance_before'], h.owned(old, box), old['pool'])
    if record['timer_was_active']: p.run(['systemctl', 'start', TIMER], input='')
    record['phase'] = 'rolled-back-awaiting-application-acceptance'; h.save(receipt, record)
    print(json.dumps({'box': box, 'pool': old['pool'], 'evidence': str(receipt),
                      'retention': 'all archives, scratch restores and receipts retained'}, indent=2))


def resume_backup(c, config_path, migration):
    h.host(c)
    directory = p.STATE / 'storage-migrations' / migration; p.no_symlinks(directory)
    receipt = directory / 'move.json'; record = json.loads(receipt.read_text())
    p.require(record['owner'] == m.OWNER and record['host'] == c['machine_id']
              and record['migration'] == migration and record['before_config'] == c
              and record['config_path'] == str(Path(config_path).absolute()), 'Migration receipt/config differs')
    p.require(record['phase'] == 'failed' and record['failed_step'] in
              ('stopped-independent-backup', 'verifying-original-rootfs') and record.get('backup'),
              'Require an interrupted verification with a completed pre-move backup')
    box = record['box']; p.box_name(box)
    p.require(record['after_config'] == {**c, 'instance_pools': {**c.get('instance_pools', {}), box: 'hdd'}},
              'Recorded destination configuration differs')
    obj = h.owned(c, box)
    p.require(obj['status'] == 'Stopped', 'Original box must still be stopped on the source pool')
    m.validate_move(record['instance_before'], obj, c['pool'])
    verified_trial(c, record['trial'], obj['config'].get('volatile.base_image'))
    check_installed(c, box)
    exported = Path(record['backup']); p.no_symlinks(exported)
    p.require(exported.name == box and exported.parent.parent == backup.backup_root(c),
              'Backup is not in the registered SSD backup tree')
    backup.storage(c, exported)
    for name in ('backup.json', 'data.tar', 'rootfs.tar.gz'): p.no_symlinks(exported / name)
    saved = json.loads((exported / 'backup.json').read_text())
    p.require(saved.get('complete') and saved['owner'] == backup.OWNER and saved['box'] == box
              and saved['machine_id'] == c['machine_id'] and saved['hdd_uuid'] == c['hdd_uuid'],
              'Backup is incomplete or belongs to another box/host')
    m.validate_move(record['instance_before'], saved['instance'], c['pool'])
    preserved = directory / 'interrupted-backup.json'
    p.require(not preserved.exists(), 'Backup continuation already attempted; inspect its receipt')
    h.save(preserved, record)
    record['resumed_from'] = str(preserved)
    record.pop('error', None); record.pop('failed_step', None)
    def phase(value):
        record['phase'] = value; h.save(receipt, record); print(value, flush=True)
    try:
        if timer_active(): p.run(['systemctl', 'stop', TIMER], input='')
        phase('verifying-retained-backup-checksums')
        p.require(p.digest(exported / 'data.tar') == saved['data_sha256']
                  and p.digest(exported / 'rootfs.tar.gz') == saved['rootfs_sha256'],
                  'Retained backup checksum differs; no move performed')
        finish_move(c, config_path, record, directory, phase)
    except (Exception, KeyboardInterrupt) as error:
        record.update(failed_step=record['phase'], phase='failed', error=str(error)); h.save(receipt, record)
        print('Continuation retained for inspection; box/timer may be stopped. Do not rerun blindly.', file=sys.stderr)
        raise


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['move', 'rollback', 'resume-backup'])
    parser.add_argument('--config', required=True); parser.add_argument('--migration', required=True)
    parser.add_argument('--box'); parser.add_argument('--trial')
    parser.add_argument('--sync-paused', action='store_true'); parser.add_argument('--apply', action='store_true')
    a = parser.parse_args(); c = p.config(a.config); p.check_host(c); p.box_name(a.migration)
    if a.command == 'move':
        p.require(a.box and a.trial, 'Supply --box and verified --trial'); p.box_name(a.box); m.t.trial_names(a.trial)
    print(json.dumps({'command': a.command, 'box': a.box, 'migration': a.migration,
                      'backup_destination': str(backup.BACKUP_HOME), 'rootfs_target': 'original pool' if a.command == 'rollback' else 'hdd',
                      'requires': 'paused laptop sync and stopped-box downtime', 'apply': a.apply}, indent=2), flush=True)
    if a.apply:
        p.require(a.sync_paused, 'Pause and reconcile laptop sync; acknowledge with --sync-paused')
        with p.locked():
            if a.command == 'move': move(c, a.config, a.box, a.trial, a.migration)
            elif a.command == 'rollback': rollback(c, a.config, a.migration)
            else: resume_backup(c, a.config, a.migration)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print('Refused:', error, file=sys.stderr); sys.exit(1)
