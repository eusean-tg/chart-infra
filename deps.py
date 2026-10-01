#!/usr/bin/env python3
"""Install frozen Linux dependencies separately from source fetch and deployment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from common import STATE, guard, lock, load, save, run
from sources import valid_name

IMAGE_TAG = 'chart-infra-node:24.20.0-pnpm11.28.2'
SERVICES = ('auth-service-backend', 'tharamine-user-service', 'orange-v2-backend', 'kiyotaka-frontend', 'script-migration')


def install(profile, workspace, service):
    guard()
    app_lock = lock('chart-apps-'+profile)
    mongo_lock = lock('chart-mongo-'+profile)
    held = lock('chart-dependency-install')
    import mongo
    mongo.configure(profile); mongo.inventory()
    source = load(f'sources/{profile}/{workspace}.json')[service]
    if source.get('sync'):
        import sync as sync_runtime
        sync_runtime.assert_selection(workspace, load(f'sources/{profile}/{workspace}.json'))
    path = Path(source['path'])
    if path.resolve() != path or path.is_symlink():
        raise SystemExit('Refusing symlinked source')
    npmrc = Path.home() / '.npmrc'
    if not npmrc.is_file() or npmrc.stat().st_mode & 0o077:
        raise SystemExit('Configure ~/.npmrc locally with read access and chmod 600 first')
    if (path / '.npmrc').exists():
        raise SystemExit('Review repository .npmrc before installation; do not override private registry routing implicitly')
    image = json.loads(run(['docker', 'image', 'inspect', IMAGE_TAG], capture=True))[0]['Id']
    h = hashlib.sha256(image.encode())
    for name in ['package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', '.pnpmfile.cjs']:
        f = path / name
        if f.exists(): h.update(name.encode() + b'\0' + f.read_bytes())
    if not (path / 'pnpm-lock.yaml').is_file():
        raise SystemExit('A frozen pnpm lockfile is required')
    mountpoint = path / 'node_modules'
    if mountpoint.is_symlink(): raise SystemExit('Source node_modules must be an empty mount point')
    mountpoint.mkdir(exist_ok=True)
    if any(mountpoint.iterdir()): raise SystemExit('Refusing to hide existing source dependencies')
    key = h.hexdigest()
    base = mongo.BASE / 'dependencies' / service / key
    if base.resolve()!=base:raise SystemExit('Symlinked dependency destination')
    stamp=base/'installed.json'
    if stamp.exists():
        cached=json.loads(stamp.read_text())
        if cached!={'image':image,'key':key} or not (base/'node_modules').is_dir():
            raise SystemExit('Dependency cache identity differs')
        save(f'dependencies/{profile}/{workspace}/{service}.json',
             {'image':image,'key':key,'source':str(path),'modules':str(base/'node_modules'),'cached':True})
        print(f'{service}: reused immutable prepared Linux dependencies ({key[:12]})')
        return
    if base.exists():raise SystemExit('Unfinished/unregistered dependency cache exists; inspect before retrying, never modify a mounted cache')
    base.mkdir(parents=True, exist_ok=True)
    modules = base / 'node_modules'
    modules.mkdir(exist_ok=True)
    # Private tokens exist only in this read-only installer mount, never an image layer.
    args = ['docker', 'run', '--rm', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp:rw,nosuid,nodev',
            '--mount', f'type=bind,src={path},dst=/app,readonly',
            '--mount', f'type=bind,src={modules},dst=/app/node_modules',
            '--mount', f'type=bind,src={npmrc},dst=/run/npmrc,readonly',
            '-e', 'NPM_CONFIG_USERCONFIG=/run/npmrc', '-e', 'HOME=/tmp', '-e', 'HUSKY=0',
            '-e', 'CI=true', '-e', 'MONGOMS_DISABLE_POSTINSTALL=1',
            image, 'pnpm', 'install', '--frozen-lockfile', '--store-dir=/app/node_modules/.pnpm-store']
    log = STATE / f'deps-{profile}-{workspace}-{service}.log'
    with log.open('w') as out:
        result = subprocess.run(args, stdout=out, stderr=subprocess.STDOUT)
    # Keep logs private and redact literal credential values, including failures.
    content = log.read_text(errors='replace')
    for line in npmrc.read_text().splitlines():
        if '=' in line and any(x in line.split('=',1)[0].lower() for x in ('token', 'password', '_auth')):
            value = line.split('=',1)[1].strip()
            if value: content = content.replace(value, '[REDACTED]')
    log.write_text(content)
    if result.returncode:
        print(content[-4000:])
        raise SystemExit(f'Install failed; private log: {log}')
    verify=hashlib.sha256(image.encode())
    for name in ['package.json','pnpm-lock.yaml','pnpm-workspace.yaml','.pnpmfile.cjs']:
        f=path/name
        if f.exists():verify.update(name.encode()+b'\0'+f.read_bytes())
    if verify.hexdigest()!=key:raise SystemExit('Dependency inputs changed during install; pause source synchronization before retrying')
    mongo.write_json(stamp,{'image':image,'key':key})
    save(f'dependencies/{profile}/{workspace}/{service}.json',
         {'image': image, 'key': key, 'source': str(path), 'modules': str(modules), 'log': str(log)})
    print(f'{service}: frozen dependencies installed ({key[:12]})')


def digest(root):
    h=hashlib.sha256();count=0
    for here,dirs,files in os.walk(root,followlinks=False):
        for name in sorted(dirs+files):
            p=Path(here)/name;rel=p.relative_to(root).as_posix()
            if p.is_symlink():value=('link:'+os.readlink(p)).encode()
            elif p.is_file():
                q=hashlib.sha256()
                with p.open('rb') as f:
                    for block in iter(lambda:f.read(1024*1024),b''):q.update(block)
                value=q.digest()
            else:continue
            h.update(rel.encode()+b'\0'+value);count+=1
        dirs.sort()
    return {'sha256':h.hexdigest(),'entries':count}


def relocate(profile, workspace, service):
    """Copy prepared Linux dependencies to HDD without deleting their old volume."""
    guard();held=lock('chart-dependency-install')
    import mongo
    mongo.configure(profile);mongo.inventory()
    record=load(f'dependencies/{profile}/{workspace}/{service}.json')
    old=Path(record['modules']);dest=mongo.BASE/'dependencies'/service/record['key']/'node_modules'
    if old==dest:
        stamp=dest.parent/'installed.json'
        if not stamp.exists():
            receipt=load(f'dependency-relocations/{profile}-{workspace}-{service}.json')
            if receipt['to']!=str(dest) or digest(dest)!={k:receipt[k] for k in ('sha256','entries')}:
                raise SystemExit('Relocation receipt differs from the retained dependency copy')
            mongo.write_json(stamp,{'image':record['image'],'key':record['key']})
        print(service+': dependencies already on HDD');return
    if old.resolve()!=old or dest.resolve()!=dest or not old.is_dir():raise SystemExit('Unsafe dependency relocation path')
    if dest.exists():raise SystemExit('Dependency destination already exists; inspect incomplete copy before retrying')
    dest.parent.mkdir(parents=True,exist_ok=True)
    run(['cp','-a',str(old),str(dest)])
    before=digest(old);after=digest(dest)
    if before!=after:raise SystemExit('Dependency copy differs; original retained; do not activate')
    save(f'dependency-relocations/{profile}-{workspace}-{service}.json',{'from':str(old),'to':str(dest),**before})
    mongo.write_json(dest.parent/'installed.json',{'image':record['image'],'key':record['key']})
    source=load(f'sources/{profile}/{workspace}.json')[service]['path']
    save(f'dependencies/{profile}/{workspace}/{service}.json',{**record,'modules':str(dest),'source':source})
    print(service+': verified HDD dependency copy; old volume retained; application selection unchanged')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['install','relocate'])
    p.add_argument('--profile', type=valid_name, default='sean')
    p.add_argument('--workspace', type=valid_name, default='pilot')
    p.add_argument('--service', choices=SERVICES, required=True)
    a = p.parse_args()
    (install if a.action=='install' else relocate)(a.profile, a.workspace, a.service)

if __name__ == '__main__': main()
