#!/usr/bin/env python3
"""Opt-in synthetic Mutagen/SSH/tsx proof in a prepared box; retains fixture files."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'incus'))
import laptop_sync as client
from sync_policy import common, manifest, fingerprint


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True)
    p.add_argument('--box', required=True)
    p.add_argument('--ssh-config', required=True)
    p.add_argument('--mutagen', required=True)
    p.add_argument('--state-dir', required=True)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    if not a.apply:
        print('Plan: create retained synthetic source workspace/session and a networkless tsx watcher; no app/data selection.')
        return
    root = Path(a.state_dir).resolve()
    root.mkdir(mode=0o700)  # A fresh fixture only; never reuse or clean another run.
    wrappers = root / 'bin'
    wrappers.mkdir()
    for executable in ('ssh', 'scp'):
        f = wrappers / executable
        f.write_text('#!/bin/sh\nexec /usr/bin/' + executable + ' -F ' + shlex.quote(str(Path(a.ssh_config).resolve())) + ' "$@"\n')
        f.chmod(0o700)
    os.environ['PATH'] = str(wrappers) + os.pathsep + os.environ['PATH']
    os.environ['MUTAGEN_SSH_PATH'] = str(wrappers)
    os.environ['MUTAGEN_DATA_DIRECTORY'] = str(root / 'daemon')
    def remote(code, data=None):
        return subprocess.check_output(['ssh', a.host, 'python3 -c ' + shlex.quote(code)], input=data, timeout=60)
    def execute(argv):
        return subprocess.check_output(['ssh', a.host, shlex.join(argv)], text=True, timeout=240)
    helper = '/opt/chart-infra/incus/'
    before = json.loads(execute(['python3', helper + 'box.py', 'status', '--box', a.box]))
    stamp = str(time.time_ns())[-12:]
    workspace = 'sync-proof-' + stamp
    fixture = root / 'source'
    (fixture / 'src').mkdir(parents=True)
    (fixture / 'package.json').write_text('{"type":"module"}')
    (fixture / 'pnpm-lock.yaml').write_text('lockfileVersion: 9')
    (fixture / 'src/index.ts').write_text('console.log("sync-proof-initial"); setInterval(()=>{},1000);\n')
    subprocess.run(['git', 'init', '-q', str(fixture)], check=True)
    subprocess.run(['git', '-C', str(fixture), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(fixture), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                    'commit', '-qm', 'synthetic fixture'], check=True)
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        for path in sorted(fixture.rglob('*')):
            rel = path.relative_to(fixture)
            if path.is_file() and not common.ignored(rel.parts):
                archive.add(path, arcname=str(rel))
    archive_path = '/srv/chart/cache/artifacts/' + workspace + '.tar'
    remote('from pathlib import Path; import sys; p=Path(' + repr(archive_path) + '); p.open("xb").write(sys.stdin.buffer.read())', data.getvalue())
    for service in common.REPOS:
        execute(['python3', helper + 'box.py', 'source', '--box', a.box, '--workspace', workspace,
                 '--service', service, '--archive', archive_path, '--sha256', hashlib.sha256(data.getvalue()).hexdigest(),
                 '--revision', subprocess.check_output(['git', '-C', str(fixture), 'rev-parse', 'HEAD'], text=True).strip(), '--apply'])
    execute(['python3', helper + 'box_sync.py', 'prepare', '--box', a.box, '--workspace', workspace, '--service', 'auth', '--apply'])
    for name in ('.env.local', '.npmrc', 'private.pem'):
        (fixture / name).write_text('SYNTHETIC_EXCLUSION_ONLY')
    (fixture / 'node_modules').mkdir()
    (fixture / 'node_modules/WRONG_PLATFORM').write_text('SYNTHETIC_EXCLUSION_ONLY')
    args = SimpleNamespace(host=a.host, box=a.box, workspace=workspace, service='auth', remote_dir=helper.rstrip('/'),
                           source=str(fixture), mutagen=a.mutagen, syncthing_config=None, timeout=45)
    statefile = root / 'session.json'
    watcher = 'chart-sync-proof-' + stamp
    state = None
    result = {'scope': 'PC-origin SSH synthetic fixture, not Mac or actual backend hot-reload acceptance',
              'workspace': workspace, 'local_state': str(root), 'passed': False}
    try:
        original_write = common.write_json
        interrupted = [False]
        def interrupt(path, value):
            if value.get('session') and not interrupted[0]:
                interrupted[0] = True
                raise RuntimeError('synthetic interruption after Mutagen create')
            original_write(path, value)
        common.write_json = interrupt
        try:
            client.setup(args, None, statefile)
        except RuntimeError as error:
            assert str(error) == 'synthetic interruption after Mutagen create'
        finally:
            common.write_json = original_write
        state = json.loads(statefile.read_text())
        client.setup(args, state, statefile)
        state = json.loads(statefile.read_text())
        identifier = state['session']
        client.setup(args, state, statefile)
        assert json.loads(statefile.read_text())['session'] == identifier
        result['interrupted_and_idempotent_setup'] = True
        client.change(args, state, 'resume')
        client.checkpoint(args, state, True)
        remote_path = state['remote']['path']
        assertion = ('from pathlib import Path; p=Path(' + repr(remote_path) + '); '
                     'assert all(not (p/n).exists() for n in [".env.local",".npmrc","private.pem","node_modules/WRONG_PLATFORM"]); '
                     'assert (p/"src/index.ts").stat().st_mode & 0o777 == 0o644; '
                     'assert (p/"src").stat().st_mode & 0o777 == 0o755')
        remote(assertion)
        result['transfer_freeze_exclusions_permissions'] = True
        source_info = json.loads(remote('import json; from pathlib import Path; p=Path("/srv/chart/data/identity/apps"); '
            'print(json.dumps({"runtime":json.loads((p/"runtime.json").read_text())["image"], '
            '"volume":json.loads((p/"selection.json").read_text())["deps"]["auth"]["volume"]}))'))
        execute(['docker', 'run', '-d', '--name', watcher, '--label', 'chart-infra.owner=chart-sync-proof',
                 '--network=none', '--read-only', '--user', '1000:1000', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                 '--tmpfs', '/tmp:rw,nosuid,nodev,mode=1777', '--workdir', '/app',
                 '--mount', 'type=bind,src=' + remote_path + ',dst=/app,readonly',
                 '--mount', 'type=volume,src=' + source_info['volume'] + ',dst=/app/node_modules,readonly',
                 source_info['runtime'], 'node', '/app/node_modules/tsx/dist/cli.mjs', 'watch', 'src/index.ts'])
        watcher_id = execute(['docker', 'inspect', '--format', '{{.Id}}', watcher]).strip()
        client.change(args, state, 'resume')
        marker = 'sync-proof-edit-' + stamp
        temp = fixture / 'src/.edit.tmp'
        temp.write_text('console.log(' + json.dumps(marker) + '); setInterval(()=>{},1000);\n')
        temp.replace(fixture / 'src/index.ts')
        (fixture / 'src/created.ts').write_text('export {}')
        client.checkpoint(args, state, False)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if marker in execute(['docker', 'logs', watcher]):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Watcher did not reload')
        assert execute(['docker', 'inspect', '--format', '{{.Id}}', watcher]).strip() == watcher_id
        result['read_only_uid1000_tsx_hot_reload_same_container'] = True
        (fixture / 'src/created.ts').rename(fixture / 'src/renamed.ts')
        client.checkpoint(args, state, False)
        (fixture / 'src/renamed.ts').unlink()  # Only the synthetic file created by this test.
        client.checkpoint(args, state, False)
        common.mutagen(a.mutagen, 'daemon', 'stop')
        common.mutagen(a.mutagen, 'daemon', 'start')
        client.checkpoint(args, state, False)
        result['rename_delete_and_isolated_daemon_reconnect'] = True
        client.checkpoint(args, state, True)
        before_hash = fingerprint(manifest(fixture))
        (fixture / 'src/index.ts').write_text('console.log("sync-proof-paused-edit"); setInterval(()=>{},1000);\n')
        remote_hash = json.loads(remote('import sys;sys.path.insert(0,' + repr(helper) + '); import sync_policy as p; '
                                      'import json;print(json.dumps(p.fingerprint(p.manifest(' + repr(remote_path) + '))))'))
        assert remote_hash == before_hash
        client.change(args, state, 'resume')
        client.checkpoint(args, state, True)
        result['paused_edit_then_reconnect_equal'] = True
        after = json.loads(execute(['python3', helper + 'box.py', 'status', '--box', a.box]))
        assert after['containers'] == before['containers']
        assert after['workspaces']['baseline'] == before['workspaces']['baseline']
        result['baseline_apps_and_source_unchanged'] = True
        result['passed'] = True
    finally:
        if state and state.get('session'):
            common.mutagen(a.mutagen, 'sync', 'pause', state['session'])
        common.mutagen(a.mutagen, 'daemon', 'stop')
        # Stop only this test's labelled watcher; keep the container and all files/volumes.
        remote('import subprocess; p=subprocess.run(["docker","ps","-q","--filter",' + repr('name=^/' + watcher + '$') +
               ',"--filter","label=chart-infra.owner=chart-sync-proof"],capture_output=True,text=True,check=True); '
               'ids=p.stdout.split(); subprocess.run(["docker","stop",*ids],check=True) if ids else None')
        common.write_json(root / 'result.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
