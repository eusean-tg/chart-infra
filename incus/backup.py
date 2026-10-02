#!/usr/bin/env python3
"""Stop-copy-restart HDD backups, verified scratch restores and scoped retention."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import time
import prep as p
import host_common as h

OWNER = 'chart-box-backup-v1'


def backup_root(c):
    root = Path(c['hdd_mount']) / 'shared-dev/backups/boxes'
    p.no_symlinks(root)
    return root


def archive_filter(member, destination):
    safe = tarfile.data_filter(member, destination)
    p.require(safe is not None, 'Unsupported backup entry')
    # Preserve numeric HDD ownership for Incus idmapped attachments; reject devices and escaping links.
    return safe.replace(uid=member.uid, gid=member.gid, uname=None, gname=None)


def backup(c, name, *, rootfs=False, resume=True, nightly=False):
    obj = h.owned(c, name)
    p.require(obj['status'] in ('Running', 'Stopped'), 'Box must be running or stopped')
    root = backup_root(c)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    directory = root / stamp / name
    directory.mkdir(parents=True, mode=0o700)
    record = {'owner': OWNER, 'box': name, 'machine_id': c['machine_id'], 'hdd_uuid': c['hdd_uuid'],
              'nightly': nightly, 'complete': False, 'created': dt.datetime.now(dt.timezone.utc).isoformat(), 'instance': obj,
              'was_running': obj['status'] == 'Running'}
    h.save(directory / 'backup.json', record)
    try:
        if record['was_running']:
            p.run(['incus', 'stop', 'local:' + name, '--project', c['project'], '--timeout', '120'])
        p.require(h.instance(c, name)['status'] == 'Stopped', 'Refusing a live filesystem copy')
        source = Path(c['boxes_root']) / name
        p.run(['tar', '--create', '--file', directory / 'data.tar', '--numeric-owner', '--acls', '--xattrs',
               '--one-file-system', '--directory', source, '.'])
        record['data_sha256'] = p.digest(directory / 'data.tar')
        if rootfs:
            p.run(['incus', 'export', 'local:' + name, directory / 'rootfs.tar.gz', '--project', c['project'], '--instance-only'])
            record['rootfs_sha256'] = p.digest(directory / 'rootfs.tar.gz')
        record['complete'] = True
        h.save(directory / 'backup.json', record)
    finally:
        if resume and record['was_running'] and h.instance(c, name)['status'] == 'Stopped':
            p.run(['incus', 'start', 'local:' + name, '--project', c['project']])
    return directory


def restore(source, destination):
    source = Path(source).absolute(); destination = Path(destination).absolute()
    p.no_symlinks(source); p.no_symlinks(destination)
    p.no_symlinks(source / 'backup.json'); p.no_symlinks(source / 'data.tar')
    record = json.loads((source / 'backup.json').read_text())
    p.require(record.get('owner') == OWNER and record.get('complete'), 'Incomplete/foreign backup')
    p.require(p.digest(source / 'data.tar') == record['data_sha256'], 'Backup checksum differs')
    p.require(not destination.exists(), 'Scratch destination must not exist')
    destination.mkdir(parents=True, mode=0o700)
    with tarfile.open(source / 'data.tar') as archive:
        archive.extractall(destination, numeric_owner=True, filter=archive_filter)
    # Compare every restored regular file/link with the archive; no app or instance is started.
    with tarfile.open(source / 'data.tar') as archive:
        for entry in archive:
            if entry.isfile():
                import hashlib
                expected = hashlib.file_digest(archive.extractfile(entry), 'sha256').hexdigest()
                p.require(p.digest(destination / entry.name) == expected, 'Restored contents differ')
            elif entry.issym():
                p.require(os.readlink(destination / entry.name) == entry.linkname, 'Restored link differs')
    return {'box': record['box'], 'verified_data_sha256': record['data_sha256'], 'scratch': str(destination)}


def prune(c, keep_days):
    p.require(keep_days >= 1, 'Retention must be positive')
    root = backup_root(c)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=keep_days)
    if not root.exists(): return
    for generation in sorted(root.iterdir()):
        if generation.is_symlink() or not generation.is_dir(): continue
        try: stamp = dt.datetime.strptime(generation.name, '%Y%m%dT%H%M%S.%fZ').replace(tzinfo=dt.timezone.utc)
        except ValueError: continue
        if stamp >= cutoff: continue
        for directory in generation.iterdir():
            if directory.is_symlink() or not directory.is_dir(): continue
            receipt = directory / 'backup.json'
            if not receipt.is_file() or receipt.is_symlink(): continue
            record = json.loads(receipt.read_text())
            if (record.get('owner') != OWNER or not record.get('complete') or not record.get('nightly') or record.get('rootfs_sha256')
                    or record.get('machine_id') != c['machine_id'] or record.get('hdd_uuid') != c['hdd_uuid']): continue
            # A newer completed backup must exist before any dated copy is collected.
            newer = [f for f in root.glob('*/' + directory.name + '/backup.json') if f.parent.parent.name > generation.name]
            if not any(json.loads(f.read_text()).get('complete') and json.loads(f.read_text()).get('owner') == OWNER for f in newer): continue
            if set(f.name for f in directory.iterdir()) != {'backup.json', 'data.tar'}: continue
            shutil.rmtree(directory)
        if not any(generation.iterdir()): generation.rmdir()


def scheduled(c):
    # Host-owned instance/device checks precede stopping any box.
    objects = p.query('/1.0/instances?project=' + c['project'] + '&recursion=1')
    failures = []
    for obj in objects:
        if (obj.get('config', {}).get('user.chart-infra') != p.OWNER
                or obj.get('config', {}).get('user.chart-box') != h.OWNER
                or 'data' not in obj.get('devices', {})): continue
        try: print(backup(c, obj['name'], nightly=True), flush=True)
        except Exception as e: failures.append(obj['name'] + ': ' + str(e))
    if failures: raise RuntimeError('\n'.join(failures))
    prune(c, 7)


def install(c):
    directory = p.STATE / 'backup-tool'
    p.require(not directory.exists(), 'Installed backup tool exists; review an explicit update')
    directory.mkdir(parents=True, mode=0o700)
    for name in ('backup.py', 'host_common.py', 'prep.py', 'versions.lock.json', 'host.example.json'):
        p.write_owned(directory / name, (p.HERE / name).read_text())
    # Scheduling has explicit user authorization: 04:00 Asia/Kuala_Lumpur, seven days.
    service = '[Unit]\nDescription=Chart personal-box HDD backups\nAfter=incus.service\n[Service]\nType=oneshot\nUMask=0077\nExecStart=/usr/bin/python3 /var/lib/chart-incus/backup-tool/backup.py nightly --config /etc/chart-incus/host.json --apply\n'
    timer = '[Unit]\nDescription=Nightly chart-box backups\n[Timer]\nOnCalendar=*-*-* 04:00:00 Asia/Kuala_Lumpur\nPersistent=false\n[Install]\nWantedBy=timers.target\n'
    p.write_owned('/etc/systemd/system/chart-box-backup.service', service, 0o644)
    p.write_owned('/etc/systemd/system/chart-box-backup.timer', timer, 0o644)
    p.run(['systemctl', 'daemon-reload'])
    p.run(['systemctl', 'enable', '--now', 'chart-box-backup.timer'])


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['backup', 'restore-check', 'nightly', 'install-timer'])
    parser.add_argument('--config', required=True); parser.add_argument('--box')
    parser.add_argument('--backup'); parser.add_argument('--scratch'); parser.add_argument('--rootfs', action='store_true')
    parser.add_argument('--apply', action='store_true')
    a = parser.parse_args(); c = p.config(a.config); p.check_host(c)
    if not a.apply:
        print('Plan:', a.command, 'HDD backup; stopped copy; retain seven nights for scheduled HDD-only backups.'); return
    with p.locked():
        h.host(c)
        if a.command == 'backup': print(backup(c, a.box, rootfs=a.rootfs))
        elif a.command == 'restore-check':
            p.require(a.backup and a.scratch, 'Supply --backup and --scratch')
            source = Path(a.backup).absolute(); scratch = Path(a.scratch).absolute()
            p.require(backup_root(c) in source.parents, 'Select an owned backup path')
            p.require(scratch.parent == Path(c['hdd_mount']) / 'shared-dev/restore-checks', 'Use a new scratch directory under shared-dev/restore-checks')
            print(json.dumps(restore(source, scratch), indent=2))
        elif a.command == 'nightly': scheduled(c)
        else: install(c)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError, tarfile.TarError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
