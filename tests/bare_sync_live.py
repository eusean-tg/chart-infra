#!/usr/bin/env python3
"""Opt-in real Mutagen policy proof using isolated local fixture directories."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/chart-box/scripts'))
import policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mutagen', required=True); parser.add_argument('--root', required=True)
    parser.add_argument('--apply', action='store_true'); a = parser.parse_args()
    root = Path(a.root).absolute()
    if not a.apply: print('Plan: isolated local Mutagen fixture; no box, application or existing session changes.'); return
    if root.exists(): raise RuntimeError('Use a new fixture directory; previous evidence is retained')
    root.mkdir(parents=True, mode=0o700)
    alpha, beta = root / 'laptop', root / 'box'; alpha.mkdir(); beta.mkdir()
    env = {**os.environ, 'MUTAGEN_DATA_DIRECTORY': str(root / 'daemon')}
    def run(*args): return subprocess.check_output([a.mutagen, *args], env=env, text=True, timeout=60)
    subprocess.run(['git', 'init', '-q', str(alpha)], check=True)
    for name in ('src/index.ts', '.env.local', '.env.sample', '.env.production.example', 'tracked.pem', 'untracked.pem', '.npmrc', '.env.d/tracked', '.env.d/private', 'dir[1]/a.key', 'node_modules/skip', 'dist/skip'):
        path = alpha / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('fixture:' + name)
    subprocess.run(['git', '-C', str(alpha), 'add', 'src/index.ts', 'tracked.pem', '.env.d/tracked', 'dir[1]/a.key'], check=True)
    (beta / '.env.local').write_text('box-private')
    tracked = policy.exceptions(alpha)
    config = root / 'config.json'; config.write_text(json.dumps({'sync': {'defaults': policy.configuration(tracked)}}))
    session = None
    try:
        assert run('version').strip() == policy.VERSION
        run('sync', 'create', '--no-global-configuration', '-c', str(config), '--name', 'bare-policy-fixture', str(alpha), str(beta))
        session = json.loads(run('sync', 'list', '--template', '{{json .}}'))[0]['identifier']
        def flush():
            run('sync', 'flush', session)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if policy.manifest(alpha, tracked) == policy.manifest(beta, tracked): return
                time.sleep(.1)
            raise RuntimeError('Included file fingerprints differ')
        flush()
        assert (beta / '.env.local').read_text() == 'box-private'
        for path in ('.npmrc', 'untracked.pem', '.env.d/private', 'node_modules/skip', 'dist/skip'):
            assert not (beta / path).exists(), path
        for path in ('tracked.pem', '.env.d/tracked', 'dir[1]/a.key', '.env.sample', '.env.production.example'):
            assert (beta / path).read_text() == (alpha / path).read_text(), path
        start = time.monotonic()
        (alpha / 'src/tmp').write_text('atomic-save'); (alpha / 'src/tmp').replace(alpha / 'src/index.ts'); flush()
        elapsed = time.monotonic() - start
        (alpha / 'src/index.ts').rename(alpha / 'src/renamed.ts'); flush()
        (alpha / 'src/renamed.ts').unlink(); flush()
        assert not (beta / 'src/index.ts').exists() and not (beta / 'src/renamed.ts').exists()
        (alpha / 'conflict.ts').write_text('base'); flush()
        run('sync', 'pause', session)
        (alpha / 'conflict.ts').write_text('laptop-change'); (beta / 'conflict.ts').write_text('box-change')
        run('sync', 'resume', session); run('sync', 'flush', session)
        report = json.loads(run('sync', 'list', '--template', '{{json .}}'))[0]
        assert report.get('conflicts') and (beta / 'conflict.ts').read_text() == 'box-change'
        result = {'passed': True, 'transport': 'isolated local-to-local Mutagen', 'version': policy.VERSION,
                  'tracked_files_and_untracked_exclusions': True, 'remote_private_retention': True,
                  'atomic_save_flush_seconds': elapsed, 'rename_delete': True, 'one_way_safe_conflict_retention': True,
                  'not_laptop_or_box_acceptance': True}
        (root / 'result.json').write_text(json.dumps(result, indent=2)); print(json.dumps(result, indent=2))
    finally:
        if session: run('sync', 'pause', session)
        run('daemon', 'stop')


if __name__ == '__main__': main()
