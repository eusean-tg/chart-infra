#!/usr/bin/env python3
"""Image-builder mitigation for blocking rsyslog socket activation."""
import json
import os
from pathlib import Path
import select
import signal
import time
import prep as p
import host_common as h

DROPIN = '/etc/systemd/system/rsyslog.service.d/chart-nonblocking.conf'
CONTENT = '[Service]\nNonBlocking=yes\n'
PROBE = r'''
import json,os,pathlib,subprocess
pid=int(subprocess.check_output(['systemctl','show','rsyslog','-p','MainPID','--value'],text=True))
assert pid>1, 'rsyslog main PID missing'
readers=[]
for path in pathlib.Path('/proc').iterdir():
    if not path.name.isdigit(): continue
    try:
        if (path/'comm').read_text().strip()=='rsyslogd': readers.append(int(path.name))
    except FileNotFoundError: continue
assert readers==[pid], 'Unexpected rsyslog processes'
inodes={line.split()[6] for line in pathlib.Path('/proc/net/unix').read_text().splitlines()[1:]
        if line.split()[-1]=='/run/systemd/journal/syslog'}
assert len(inodes)==1, 'Expected one syslog socket'
result={'pid':pid,'inode':next(iter(inodes)), 'flags':{}}
for label,n in [('systemd',1),('rsyslog',pid)]:
    flags=[]
    for fd in pathlib.Path(f'/proc/{n}/fd').iterdir():
        try: match=os.readlink(fd)=='socket:['+result['inode']+']'
        except FileNotFoundError: continue
        if match:
            info=pathlib.Path(f'/proc/{n}/fdinfo/{fd.name}').read_text()
            flags.append(int(next(x.split()[1] for x in info.splitlines() if x.startswith('flags:')),8))
    assert flags, label+' syslog fd absent'
    result['flags'][label]=flags
print(json.dumps(result))
'''
INSTALL = r'''
import os,pathlib,sys
path=pathlib.Path(sys.argv[1]); content=sys.argv[2]
for parent in [path,*path.parents]: assert not parent.is_symlink(), 'Symlinked drop-in path'
if path.exists(): assert path.read_text()==content, 'Existing drop-in differs'
else:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f: f.write(content); f.flush(); os.fsync(f.fileno())
    path.chmod(0o644)
'''


def guest(c, name, args):
    return p.run(['timeout', '--foreground', '--kill-after=5', '45', 'incus', 'exec', 'local:' + name,
                  '--project', c['project'], '--disable-stdin', '--force-noninteractive', '--', *args], input='')


def nonblocking(probe):
    return all(probe['flags'].get(label) and all(value & os.O_NONBLOCK for value in probe['flags'][label])
               for label in ('systemd', 'rsyslog'))


def mapped_process(init_pid, guest_pid, root=Path('/proc')):
    namespace = os.readlink(root / str(init_pid) / 'ns/pid')
    matches = []
    for path in root.iterdir():
        if not path.name.isdigit(): continue
        try:
            if os.readlink(path / 'ns/pid') != namespace: continue
            fields = dict(line.split(':', 1) for line in (path / 'status').read_text().splitlines())
            ids = fields.get('NSpid', '').split()
            if len(ids) < 2 or int(ids[-1]) != guest_pid: continue
            p.require(fields.get('Name', '').strip() == 'rsyslogd', 'Mapped process is not rsyslog')
            p.require(Path(os.readlink(path / 'exe')).name == 'rsyslogd', 'Mapped executable differs')
            matches.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError): continue
    p.require(len(matches) == 1, 'Expected one host PID matching guest rsyslog and PID namespace')
    return matches[0]


def terminate_reader(c, name, guest_pid, validate):
    validate()
    state = p.query(f'/1.0/instances/{name}/state?project={c["project"]}')
    p.require(state['status'] == 'Running', 'Target must remain running')
    pid = mapped_process(state['pid'], guest_pid)
    fd = os.pidfd_open(pid)
    try:
        poll = select.poll(); poll.register(fd, select.POLLIN)
        p.require(not poll.poll(0), 'rsyslog already exited; inspect before retry')
        p.require(mapped_process(state['pid'], guest_pid) == pid, 'Process mapping changed')
        validate()
        # Host root is the signal sender; the pidfd prevents numeric PID reuse.
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        p.require(bool(poll.poll(30000)), 'rsyslog did not exit after TERM; no KILL fallback')
    finally:
        os.close(fd)
    return pid


def mitigate(c, name, validate, receipt, durable=False):
    validate()
    record = {'box': name, 'phase': 'inspect', 'dropin': DROPIN}
    h.save(receipt, record)
    try:
        record['before'] = json.loads(guest(c, name, ['python3', '-c', PROBE]))
        record['phase'] = 'install-dropin'; h.save(receipt, record)
        guest(c, name, ['python3', '-c', INSTALL, DROPIN, CONTENT])
        if durable:
            guest(c, name, ['python3', '-c', INSTALL,
                           '/srv/chart/data/private/systemd/chart-nonblocking.conf', CONTENT])
        guest(c, name, ['systemctl', 'daemon-reload'])
        record['phase'] = 'terminate-rsyslog'; h.save(receipt, record)
        record['host_pid_terminated'] = terminate_reader(c, name, record['before']['pid'], validate)
        guest(c, name, ['python3', '-c',
              'import subprocess,sys,time\n'
              'for i in range(100):\n'
              ' pid=subprocess.check_output(["systemctl","show","rsyslog","-p","MainPID","--value"],text=True).strip()\n'
              ' if pid!=sys.argv[1]: break\n'
              ' time.sleep(.1)\nelse: raise RuntimeError("Manager has not observed rsyslog exit")',
              str(record['before']['pid'])])
        record['phase'] = 'start-rsyslog'; h.save(receipt, record)
        guest(c, name, ['systemctl', 'start', 'rsyslog'])
        guest(c, name, ['systemctl', 'is-active', '--quiet', 'rsyslog'])
        record['after'] = json.loads(guest(c, name, ['python3', '-c', PROBE]))
        p.require(record['after']['pid'] != record['before']['pid'], 'rsyslog was not replaced')
        p.require(nonblocking(record['after']), 'Syslog descriptors remain blocking')
        token = 'chart-syslog-rollout-' + str(time.time_ns())
        guest(c, name, ['logger', '-t', 'chart-infra', token])
        guest(c, name, ['python3', '-c',
              'import pathlib,sys,time; p=pathlib.Path("/var/log/syslog")\n'
              'for i in range(50):\n if p.exists() and sys.argv[1] in p.read_text(errors="replace"): break\n'
              ' time.sleep(.1)\nelse: raise RuntimeError("Log delivery failed")', token])
        record.update(phase='verified', log_delivery='passed'); h.save(receipt, record)
        return record
    except (Exception, KeyboardInterrupt) as error:
        record.update(failed_step=record['phase'], phase='failed', error=str(error))
        h.save(receipt, record)
        raise
