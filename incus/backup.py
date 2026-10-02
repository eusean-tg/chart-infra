#!/usr/bin/env python3
"""Copy stopped-box HDD data to the host SSD; verify restores and scope retention."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import resource
import subprocess
import sys
import tarfile
import time
import prep as p
import host_common as h

OWNER = 'chart-box-backup-v1'
BACKUP_HOME = Path('/var/backups/chart-incus')
MARGIN = 1024 ** 3


def backup_root(c):
    root = BACKUP_HOME / 'boxes'
    p.no_symlinks(root)
    return root


def scratch_root():
    return BACKUP_HOME / 'restore-checks'


def storage(c, path, required=0):
    path = Path(path); p.no_symlinks(path)
    ancestor = path
    while not ancestor.exists(): ancestor = ancestor.parent
    device = ancestor.stat().st_dev
    p.require(device == Path('/').stat().st_dev and device != Path(c['hdd_mount']).stat().st_dev,
              'Backup/restore destination must be on the host root SSD, separate from the HDD')
    disk = shutil.disk_usage(ancestor)
    budget = disk.free - (disk.total + 3) // 4 - MARGIN
    p.require(budget > required, 'Insufficient SSD capacity; preserve 25% free plus 1 GiB copy margin')
    return budget


def copy_command(c, args):
    budget = storage(c, backup_root(c))
    # Limit each archive's growth; partial archives remain for diagnosis on failure.
    def ceiling():
        _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        limit = budget if hard == resource.RLIM_INFINITY else min(budget, hard)
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    result = subprocess.run([str(x) for x in args], text=True, capture_output=True, preexec_fn=ceiling)
    p.require(result.returncode == 0, 'Backup copy failed; partial archive retained: ' + (result.stderr or str(result.returncode)))
    storage(c, backup_root(c))


def enrollments(c):
    path = p.STATE / 'backup-enrollment.json'; p.no_symlinks(path)
    if not path.exists(): return []
    record = json.loads(path.read_text())
    p.require(record.get('owner') == OWNER and record.get('machine_id') == c['machine_id'], 'Foreign backup enrollment')
    names = record['boxes']
    p.require(isinstance(names, list) and len(names) == len(set(names)), 'Invalid backup enrollment')
    for name in names: eligible(name)
    return names


def eligible(name):
    p.box_name(name)
    p.require(name != 'sean-dev-pilot' and not name.startswith(('chart-test-', 'chart-bare-', 'retained-')),
              'Managed pilot, image fixtures and retained instances cannot join nightly backups')


def enrollment(c, name, remove=False):
    eligible(name)
    names = enrollments(c)
    if remove:
        names = [n for n in names if n != name]
    else:
        obj = h.owned(c, name)
        p.require(obj['config'].get('user.chart-box') == h.OWNER, 'Only personal boxes can enroll')
        if name not in names: names.append(name)
    h.save(p.STATE / 'backup-enrollment.json', {'owner': OWNER, 'machine_id': c['machine_id'], 'boxes': sorted(names)})


def archive_filter(member, destination):
    safe = tarfile.data_filter(member, destination)
    p.require(safe is not None, 'Unsupported backup entry')
    # Preserve numeric HDD ownership for Incus idmapped attachments; reject devices and escaping links.
    return safe.replace(uid=member.uid, gid=member.gid, uname=None, gname=None)


def backup(c, name, *, rootfs=False, resume=True, nightly=False):
    obj = h.owned(c, name)
    p.require(obj['status'] in ('Running', 'Stopped'), 'Box must be running or stopped')
    root = backup_root(c)
    storage(c, root)
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
        size = int(p.run(['du', '--summarize', '--apparent-size', '--block-size=1', '--one-file-system', source]).split()[0])
        storage(c, root, size + size // 10)
        copy_command(c, ['tar', '--create', '--file', directory / 'data.tar', '--numeric-owner', '--acls', '--xattrs',
               '--one-file-system', '--directory', source, '.'])
        record['data_sha256'] = p.digest(directory / 'data.tar')
        if rootfs:
            copy_command(c, ['incus', 'export', 'local:' + name, directory / 'rootfs.tar.gz', '--project', c['project'], '--instance-only'])
            record['rootfs_sha256'] = p.digest(directory / 'rootfs.tar.gz')
        record['complete'] = True
        h.save(directory / 'backup.json', record)
    finally:
        if resume and record['was_running'] and h.instance(c, name)['status'] == 'Stopped':
            p.run(['incus', 'start', 'local:' + name, '--project', c['project']])
    return directory


def restore(source, destination, c=None):
    source = Path(source).absolute(); destination = Path(destination).absolute()
    p.no_symlinks(source); p.no_symlinks(destination)
    p.no_symlinks(source / 'backup.json'); p.no_symlinks(source / 'data.tar')
    record = json.loads((source / 'backup.json').read_text())
    p.require(record.get('owner') == OWNER and record.get('complete'), 'Incomplete/foreign backup')
    p.require(p.digest(source / 'data.tar') == record['data_sha256'], 'Backup checksum differs')
    p.require(not destination.exists(), 'Scratch destination must not exist')
    if c is not None:
        with tarfile.open(source / 'data.tar') as archive:
            needed = sum(entry.size + 4096 for entry in archive)
        storage(c, destination, needed)
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
    # Only explicit host enrollment can authorize automatic stops.
    names = enrollments(c)
    for name in names:
        obj = h.owned(c, name)
        p.require(obj['config'].get('user.chart-box') == h.OWNER, 'Enrolled personal-box ownership differs')
    failures = []
    for name in names:
        try: print(backup(c, name, nightly=True), flush=True)
        except Exception as e: failures.append(name + ': ' + str(e))
    if failures: raise RuntimeError('\n'.join(failures))
    prune(c, 7)


def install(c):
    p.require(enrollments(c), 'Enroll at least one accepted personal box before installing the timer')
    storage(c, backup_root(c))
    directory = p.STATE / 'backup-tool'
    p.require(not directory.exists(), 'Installed backup tool exists; review an explicit update')
    directory.mkdir(parents=True, mode=0o700)
    for name in ('backup.py', 'host_common.py', 'prep.py', 'versions.lock.json', 'host.example.json'):
        p.write_owned(directory / name, (p.HERE / name).read_text())
    # Scheduling has explicit user authorization: 04:00 Asia/Kuala_Lumpur, seven days.
    service = '[Unit]\nDescription=Chart personal-box HDD backups to SSD\nAfter=incus.service\n[Service]\nType=oneshot\nUMask=0077\nExecStart=/usr/bin/python3 /var/lib/chart-incus/backup-tool/backup.py nightly --config /etc/chart-incus/host.json --apply\n'
    timer = '[Unit]\nDescription=Nightly chart-box backups\n[Timer]\nOnCalendar=*-*-* 04:00:00 Asia/Kuala_Lumpur\nPersistent=false\n[Install]\nWantedBy=timers.target\n'
    p.write_owned('/etc/systemd/system/chart-box-backup.service', service, 0o644)
    p.write_owned('/etc/systemd/system/chart-box-backup.timer', timer, 0o644)
    p.run(['systemctl', 'daemon-reload'])
    p.run(['systemctl', 'enable', '--now', 'chart-box-backup.timer'])


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['backup', 'restore-check', 'nightly', 'install-timer', 'enroll', 'unenroll'])
    parser.add_argument('--config', required=True); parser.add_argument('--box')
    parser.add_argument('--backup'); parser.add_argument('--scratch'); parser.add_argument('--rootfs', action='store_true')
    parser.add_argument('--apply', action='store_true')
    a = parser.parse_args(); c = p.config(a.config); p.check_host(c)
    if a.command in ('backup', 'enroll', 'unenroll'):
        p.require(a.box, 'Supply --box'); p.box_name(a.box)
    if not a.apply:
        print('Plan:', a.command, 'stopped HDD copy to', backup_root(c), '; explicit nightly enrollment; retain seven nights.'); return
    with p.locked():
        h.host(c)
        if a.command == 'backup': print(backup(c, a.box, rootfs=a.rootfs))
        elif a.command == 'restore-check':
            p.require(a.backup and a.scratch, 'Supply --backup and --scratch')
            source = Path(a.backup).absolute(); scratch = Path(a.scratch).absolute()
            p.require(backup_root(c) in source.parents, 'Select an owned backup path')
            p.require(scratch.parent == scratch_root(), 'Use a new scratch directory under ' + str(scratch_root()))
            print(json.dumps(restore(source, scratch, c), indent=2))
        elif a.command == 'nightly': scheduled(c)
        elif a.command in ('enroll', 'unenroll'): enrollment(c, a.box, remove=a.command == 'unenroll')
        else: install(c)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError, tarfile.TarError) as e:
        print(str(e), file=sys.stderr); sys.exit(1)
