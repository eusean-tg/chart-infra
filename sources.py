#!/usr/bin/env python3
"""Explicit source preparation. Never invoked by application up/down."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import hashlib

from common import STATE, guard, lock, run, save

ROOT = Path(__file__).resolve().parent
BASE = Path('/mnt/hdd/shared-dev/profiles')


def valid_name(value):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', value):
        raise argparse.ArgumentTypeError('Use 1–32 lowercase letters, digits or hyphens, starting with a letter')
    return value


def prepare(profile, workspace):
    guard()
    held = lock('chart-sources-' + profile)
    import mongo
    mongo.configure(profile); mongo.inventory()
    specs = json.loads((ROOT / 'sources.lock.json').read_text())
    target = BASE / profile / 'source/workspaces' / workspace
    if target.resolve() != target:
        raise SystemExit('Refusing a symlinked source destination')
    target.mkdir(parents=True, exist_ok=True)
    inventory = {}
    for name, spec in specs.items():
        path = target / name
        if path.exists():
            if path.is_symlink() or not (path / '.git').is_dir():
                raise SystemExit(f'{name}: existing path is not an independent checkout; refusing overwrite')
            origin = run(['git', '-C', str(path), 'remote', 'get-url', 'origin'], capture=True).strip()
            if origin != spec['url']:
                raise SystemExit(f'{name}: unexpected origin; refusing overwrite')
        else:
            # Partial attempts remain visible; nothing is deleted or reset on failure.
            path.mkdir()
            run(['git', 'init', '-b', 'chart/' + workspace, str(path)])
            run(['git', '-C', str(path), 'remote', 'add', 'origin', spec['url']])
            run(['git', '-C', str(path), 'fetch', '--depth=1', 'origin', spec['revision']])
            run(['git', '-C', str(path), 'checkout', '-b', 'chart/' + workspace, 'FETCH_HEAD'])
        revision = run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture=True).strip()
        if revision != spec['revision']:
            raise SystemExit(f'{name}: revision differs from the pin; use a new workspace, never reset this one')
        dirty = bool(run(['git', '-C', str(path), 'status', '--porcelain'], capture=True))
        inventory[name] = {'path': str(path), 'revision': revision, 'dirty': dirty}
        print(f'{name}: {revision[:12]}' + (' (local edits preserved)' if dirty else ''))
    save(f'sources/{profile}/{workspace}.json', inventory)
    print(f'Source prepared at {target}. Dependencies and application deployment are separate commands.')


def relocate(profile, workspace):
    """Copy backend checkouts to HDD, preserving Git history/edits and old originals."""
    guard()
    held = lock('chart-sources-' + profile)
    import mongo
    from common import load
    from sync_common import REPOS
    mongo.configure(profile); mongo.inventory()
    inventory = load(f'sources/{profile}/{workspace}.json')
    for repo in REPOS.values():
        old = Path(inventory[repo]['path'])
        new = BASE / profile / 'source/workspaces' / workspace / repo
        if old == new: continue
        if old.resolve()!=old or new.resolve()!=new or not (old/'.git').is_dir():
            raise SystemExit('Refusing symlinked or non-checkout source relocation')
        if new.exists(): raise SystemExit(f'Existing relocation target needs inspection: {new}')
        def digest(root):
            result = {}
            for p in sorted(root.rglob('*')):
                rel=p.relative_to(root)
                if 'node_modules' in rel.parts: continue
                if p.is_symlink(): result[str(rel)]='symlink:'+os.readlink(p)
                elif p.is_file(): result[str(rel)]=hashlib.sha256(p.read_bytes()).hexdigest()
            return result
        before=digest(old)
        shutil.copytree(old,new,symlinks=True,ignore=shutil.ignore_patterns('node_modules'))
        (new/'node_modules').mkdir(exist_ok=True)
        if before!=digest(new) or before!=digest(old):raise SystemExit('Source changed during copy; do not activate')
        save(f'source-relocations/{profile}-{workspace}-{repo}.json',{'from':str(old),'to':str(new),'hashes':before})
        inventory[repo]={**inventory[repo],'path':str(new)}
        save(f'sources/{profile}/{workspace}.json',inventory)
        print(f'{repo}: verified HDD copy; original retained; app selection unchanged')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'status', 'relocate'])
    p.add_argument('--profile', type=valid_name, default='sean')
    p.add_argument('--workspace', type=valid_name, default='pilot')
    args = p.parse_args()
    if args.action == 'prepare':
        prepare(args.profile, args.workspace)
    elif args.action == 'relocate':
        relocate(args.profile, args.workspace)
    else:
        path = STATE / 'sources' / args.profile / (args.workspace + '.json')
        print(path.read_text() if path.exists() else 'Source workspace not prepared')


if __name__ == '__main__':
    main()
