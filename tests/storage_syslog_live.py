#!/usr/bin/env python3
"""Test nonblocking syslog activation on a retained synthetic HDD storage fixture."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import prep as p
import host_common as h
import storage_trial as t

DROPIN = '/etc/systemd/system/rsyslog.service.d/chart-storage-nonblocking.conf'
CONTENT = '[Service]\nNonBlocking=yes\n'
CHECK = r'''
import hashlib,json,os,pathlib,subprocess
root=pathlib.Path('/root/chart-storage-fixture')
result=json.loads((root/'result.json').read_text())
digest=hashlib.sha256()
for path in sorted(root.glob('*/*')): digest.update(path.read_bytes())
assert digest.hexdigest()==result['sha256'], 'Fixture content changed'
inodes={line.split()[6] for line in pathlib.Path('/proc/net/unix').read_text().splitlines()[1:]
        if line.split()[-1]=='/run/systemd/journal/syslog'}
assert len(inodes)==1, 'Expected one syslog socket'
rsyslog=subprocess.check_output(['systemctl','show','rsyslog','-p','MainPID','--value'],text=True).strip()
assert rsyslog.isdigit() and int(rsyslog)>1, 'rsyslog has no main PID'
flags={}
for label,pid in [('systemd','1'),('rsyslog',rsyslog)]:
    flags[label]=[]
    for fd in (pathlib.Path('/proc')/pid/'fd').iterdir():
        try: link=os.readlink(fd)
        except FileNotFoundError: continue
        if link=='socket:['+next(iter(inodes))+']':
            info=(pathlib.Path('/proc')/pid/'fdinfo'/fd.name).read_text()
            value=int(next(x.split()[1] for x in info.splitlines() if x.startswith('flags:')),8)
            assert value & os.O_NONBLOCK, label+' syslog socket is blocking'
            flags[label].append(oct(value))
    assert flags[label], label+' syslog descriptor not found'
print(json.dumps({'sha256':result['sha256'],'syslog_socket_inode':next(iter(inodes)),
                  'syslog_fd_flags':flags}))
'''


def guest(c, name, args):
    return p.run(['timeout', '--foreground', '--kill-after=5', '45', 'incus', 'exec', 'local:' + name,
                  '--project', c['project'], '--disable-stdin', '--force-noninteractive', '--', *args], input='')


def experiment_path(directory, attempt):
    p.require(re.fullmatch(r'[a-z][a-z0-9-]{0,19}', attempt), 'Use a short lowercase attempt name')
    return directory / ('syslog-experiment.json' if attempt == 'initial' else f'syslog-experiment-{attempt}.json')


def execute(c, trial, attempt='initial'):
    h.host(c)
    name = t.trial_names(trial)[1]
    directory = p.STATE / 'storage-trials' / trial
    p.no_symlinks(directory)
    original = json.loads((directory / 'proof.json').read_text())
    p.require(original.get('owner') == t.OWNER and original.get('host') == c['machine_id']
              and original.get('trial') == trial and original.get('pool') == t.POOL
              and original.get('source') == str(t.target(c)), 'Original trial identity differs')
    h.candidate(c, original['image'], verified=True)
    t.validate_pool(c, p.query('/1.0/storage-pools/' + t.POOL))
    spec = t.fixture_spec(c, name, t.POOL, original['image'], trial)
    p.require(t.validate_fixture(c, name, spec)['status'] == 'Running', 'Expected retained running HDD fixture')
    proof = experiment_path(directory, attempt)
    p.require(not proof.exists(), 'Experiment receipt exists; inspect before another attempt')
    record = {'instance': name, 'owner': t.OWNER, 'image': original['image'], 'attempt': attempt,
              'phase': 'collecting', 'cycles': [], 'dropin': DROPIN}
    h.save(proof, record)
    try:
        record['original_measurement'] = json.loads(guest(c, name, ['cat', '/root/chart-storage-fixture/result.json']))
        record['original_journal'] = guest(c, name, ['journalctl', '-b', '--no-pager', '-n', '150'])
        record['phase'] = 'installing-fixture-dropin'; h.save(proof, record)
        guest(c, name, ['python3', '-c',
              'import pathlib,sys; p=pathlib.Path(sys.argv[1]); '
              'p.parent.mkdir(parents=True,exist_ok=True); '
              'f=p.open("x"); f.write(sys.argv[2]); f.close()', DROPIN, CONTENT])
        t.validate_fixture(c, name, spec)
        record['phase'] = 'force-stopping-synthetic-fixture'; h.save(proof, record)
        # PID 1 is stuck. Only this validated synthetic fixture is force-stopped.
        p.run(['timeout', '60', 'incus', 'stop', 'local:' + name, '--project', c['project'], '--force'])
        for cycle in range(1, 4):
            record['phase'] = f'booting-{cycle}'; h.save(proof, record)
            p.require(t.validate_fixture(c, name, spec)['status'] == 'Stopped', 'Expected stopped fixture')
            p.run(['incus', 'start', 'local:' + name, '--project', c['project']])
            h.wait_ready(c, name)
            record['phase'] = f'checking-runtime-{cycle}'; h.save(proof, record)
            guest(c, name, ['systemctl', 'is-active', '--quiet', 'rsyslog'])
            result = json.loads(guest(c, name, ['python3', '-c', CHECK]))
            p.require(result['sha256'] == record['original_measurement']['sha256'], 'Original content hash differs')
            guest(c, name, ['sh', '-ec', 'systemctl start docker; docker start -a chart-storage-fixture'])
            token = f'chart-storage-syslog-{cycle}-{time.time_ns()}'
            guest(c, name, ['logger', '-t', 'chart-storage-trial', token])
            guest(c, name, ['python3', '-c',
                  'import pathlib,sys,time; p=pathlib.Path("/var/log/syslog"); '
                  '\nfor i in range(50):\n if p.exists() and sys.argv[1] in p.read_text(errors="replace"): break'
                  '\n time.sleep(.1)\nelse: raise RuntimeError("rsyslog file delivery failed")', token])
            t.validate_fixture(c, name, spec)
            record['phase'] = f'graceful-stop-{cycle}'; h.save(proof, record)
            start = time.monotonic()
            p.run(['incus', 'stop', 'local:' + name, '--project', c['project'], '--timeout', '120'])
            p.require(t.validate_fixture(c, name, spec)['status'] == 'Stopped', 'Graceful shutdown incomplete')
            record['cycles'].append({**result, 'cycle': cycle, 'stop_seconds': time.monotonic() - start,
                                     'docker': 'passed', 'rsyslog_delivery': 'passed'})
            h.save(proof, record)
        record['phase'] = 'experiment-passed-fixture-retained'
        h.save(proof, record)
        print(json.dumps({'evidence': str(proof), 'cycles': record['cycles'],
                          'scope': 'modified synthetic fixture only; original image/trial not accepted'}, indent=2))
    except (Exception, KeyboardInterrupt) as error:
        record.update(failed_step=record['phase'], phase='failed', error=str(error))
        h.save(proof, record)
        raise


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--trial', required=True)
    parser.add_argument('--attempt', default='initial')
    parser.add_argument('--apply', action='store_true')
    a = parser.parse_args(); c = p.config(a.config); p.check_host(c)
    experiment_path(Path('.'), a.attempt)
    print(json.dumps({'fixture': t.trial_names(a.trial)[1], 'attempt': a.attempt, 'dropin': CONTENT,
                      'force_stop': 'once, synthetic fixture only', 'graceful_shutdown_cycles': 3,
                      'retention': 'fixture, original proof and experiment evidence retained',
                      'apply': a.apply}, indent=2), flush=True)
    if a.apply:
        with p.locked(): execute(c, a.trial, a.attempt)


if __name__ == '__main__':
    try: main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print('Refused:', error, file=sys.stderr); sys.exit(1)
