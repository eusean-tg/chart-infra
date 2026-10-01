#!/usr/bin/env python3
"""Run on the laptop: inspect/pair/check only explicitly selected backend folders."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from sync_common import API, IGNORES, REPOS, check_roots, fingerprint, manifest, ready, unrelated, write_json


def remote(args, action, payload=None):
    command = shlex.join([str(Path(args.remote_dir) / 'chart'), 'sync', action, '--profile', args.profile])
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '--', args.host, command],
                       input=json.dumps(payload) if payload is not None else None,
                       text=True, stdout=subprocess.PIPE, check=True)
    return json.loads(p.stdout)


def local_api(config):
    if config:
        path = Path(config).expanduser().resolve(strict=True)
    else:
        choices = [Path.home()/'Library/Application Support/Syncthing/config.xml',
                   Path.home()/'.local/state/syncthing/config.xml', Path.home()/'.config/syncthing/config.xml']
        choices = [p for p in choices if p.is_file()]
        if len(choices) != 1:
            raise SystemExit('Specify --config /absolute/path/to/the/running/Syncthing/config.xml; do not guess between instances')
        path = choices[0]
    gui = ET.parse(path).getroot().find('gui')
    address = gui.findtext('address')
    if address.startswith('0.0.0.0:'): address = address.replace('0.0.0.0:', '127.0.0.1:', 1)
    if '://' not in address: address = ('https://' if gui.get('tls')=='true' else 'http://') + address
    return API(address, gui.findtext('apikey'))


def plan(args, api, saved):
    export = remote(args, 'export')
    if saved and (saved['remote'] != export or saved['host'] != args.host or saved['remote_dir'] != args.remote_dir):
        raise SystemExit('Remote identity/endpoint differs from retained pairing; inspect before changing')
    cfg = api.call('config'); device = api.call('system/status')['myID']
    if saved and saved['device_id'] != device: raise SystemExit('Local device identity differs')
    supplied = {svc:getattr(args,svc) for svc in REPOS}
    if not all(supplied.values()):
        if not saved: raise SystemExit('Supply --auth PATH --tharamine PATH --orange PATH; no workspace convention is assumed')
        supplied = saved['paths']
    own = {f['id'] for f in export['folders'].values()}
    for f in cfg['folders']:
        if f['id'] in own and not saved: raise SystemExit('Folder ID already exists without an ownership record; refusing adoption')
    existing = [(f['id'],f['path']) for f in cfg['folders'] if f['id'] not in own]
    paths = check_roots(supplied, existing)
    if saved and saved['paths'] != paths: raise SystemExit('Stored repo paths differ; refusing implicit relocation')
    for svc, path in paths.items():
        root = Path(path); ignore = root / '.stignore'
        if ignore.exists() and ignore.read_text().splitlines() != IGNORES:
            raise SystemExit(f'{svc}: existing .stignore needs explicit reconciliation; it will not be overwritten')
        manifest(root)  # Reject symlinks/conflicts before writing configuration.
        for f in cfg['folders']:
            if f['id'] == export['folders'][svc]['id']:
                if Path(f['path']).expanduser().resolve() != root or f['type'] != 'sendonly':
                    raise SystemExit('Owned folder path/type unexpectedly changed')
                if {d['deviceID'] for d in f['devices']} - {device,export['device_id']}:
                    raise SystemExit('Unexpected third-party source-folder share')
    if any(d['deviceID']==export['device_id'] for d in cfg['devices']) and not saved:
        raise SystemExit('Remote device already exists without ownership record; refusing adoption')
    return {'host':args.host,'remote_dir':args.remote_dir,'remote':export,'device_id':device,'paths':paths}, cfg


def pair(args, api, saved, statefile):
    owned, before = plan(args, api, saved)
    exp = owned['remote']; folder_ids = {f['id'] for f in exp['folders'].values()}
    snapshot = unrelated(before, folder_ids, exp['device_id'])
    if not saved:
        write_json(statefile.parent/'before-config.private.json', before)
        owned['phase']='prepared'; write_json(statefile, owned)
    # API changes are scoped to owned objects, never PUT the whole config.
    try:
        for path in owned['paths'].values():
            p=Path(path)/'.stignore'
            if not p.exists(): p.write_text('\n'.join(IGNORES)+'\n')
        remote(args,'pair',{'device_id':owned['device_id'],'paths':owned['paths']})
        if not any(d['deviceID']==exp['device_id'] for d in before['devices']):
            d=api.call('config/defaults/device');d.update({'deviceID':exp['device_id'],
                'name':'chart-'+args.profile,'addresses':[exp['address']], 'introducer':False,
                'autoAcceptFolders':False,'paused':False})
            api.call('config/devices','POST',d)
        for svc,f in exp['folders'].items():
            prior=next((x for x in before['folders'] if x['id']==f['id']),None)
            obj=prior or api.call('config/defaults/folder')
            obj.update({'id':f['id'],'label':f['id'],'path':owned['paths'][svc],'type':'sendonly',
                'paused':True,'devices':[{'deviceID':owned['device_id']},{'deviceID':exp['device_id']}],
                'fsWatcherEnabled':True,'rescanIntervalS':300,'ignorePerms':True,'ignoreDelete':False,
                'filesystemType':'basic','versioning':{'type':'','params':{}},'maxConflicts':-1})
            api.call('config/folders','POST',obj)
        after=api.call('config')
        if unrelated(after,folder_ids,exp['device_id']) != snapshot:
            raise SystemExit('Unrelated Syncthing config changed concurrently; new folders left paused; inspect before continuing')
        for f in exp['folders'].values():
            ignores=api.call('db/ignores',folder=f['id'])['ignore']
            if ignores!=IGNORES:raise SystemExit('Exclusions not active; new folders remain paused')
        owned['phase']='paired-paused';write_json(statefile,owned)
        print('Paired. New folders remain paused. Run resume, then wait. Existing global settings/folders preserved.')
    except BaseException:
        # Never revert a full config snapshot over concurrent unrelated changes.
        print('Setup incomplete: retain state and rerun pair to reconcile. Do not delete existing config.',file=sys.stderr)
        raise


def pause(args,api,saved,paused):
    if not saved:raise SystemExit('Run plan and pair first')
    plan(args,api,saved)
    if paused:
        remote(args,'pause')
    for f in saved['remote']['folders'].values():api.call('config/folders/'+f['id'],'PATCH',{'paused':paused})
    if not paused:remote(args,'resume')
    print('Paused.' if paused else 'Resumed; run wait before remote tests or activation.')


def wait(args,api,saved):
    if not saved:raise SystemExit('Run pair first')
    plan(args,api,saved)
    for f in saved['remote']['folders'].values():api.call('db/scan','POST',folder=f['id'])
    deadline=time.monotonic()+args.timeout
    while time.monotonic()<deadline:
        complete=True
        for f in saved['remote']['folders'].values():
            stats=api.call('db/status',folder=f['id']);comp=api.call('db/completion',folder=f['id'],device=saved['remote']['device_id'])
            complete &= ready(stats) and comp.get('remoteState')=='valid' and comp.get('needItems',1)==0
        peer=remote(args,'status')
        complete &= peer['connected'] and all(f['ready'] for f in peer['folders'].values())
        if complete:
            repos={}
            for svc,path in saved['paths'].items():
                files=manifest(path)
                head=subprocess.run(['git','-C',path,'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
                dirty=bool(subprocess.run(['git','-C',path,'status','--porcelain'],capture_output=True,text=True,check=True).stdout)
                repos[svc]={'fingerprint':fingerprint(files),'head':head,'dirty':dirty}
            try:
                result=remote(args,'checkpoint',{'device_id':saved['device_id'],'repos':repos})
            except subprocess.CalledProcessError:
                time.sleep(2);continue
            print(json.dumps(result,indent=2));return
        time.sleep(2)
    raise SystemExit('Timed out: no complete matching source checkpoint. Inspect status/errors; do not force override/revert.')


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','pair','resume','pause','status','wait'])
    p.add_argument('--host',required=True,help='SSH user@PC, reachable from this laptop')
    p.add_argument('--remote-dir',required=True,help='Absolute chart-infra path on the PC')
    p.add_argument('--profile',required=True);p.add_argument('--config',help='Local Syncthing config.xml; API key stays local')
    for svc in REPOS:p.add_argument('--'+svc,help='Explicit local backend checkout path')
    p.add_argument('--timeout',type=int,default=180)
    a=p.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',a.profile):p.error('Invalid profile')
    if a.host.startswith('-') or not Path(a.remote_dir).is_absolute():p.error('Use a normal SSH destination and absolute remote directory')
    state=Path.home()/'.local/state/chart-infra/laptop-sync'/a.profile
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=(state/'setup.lock').open('a');fcntl.flock(fd,fcntl.LOCK_EX)
    statefile=state/'pairing.json';saved=json.loads(statefile.read_text()) if statefile.exists() else None
    api=local_api(a.config)
    if a.action=='plan':print(json.dumps(plan(a,api,saved)[0],indent=2))
    elif a.action=='pair':pair(a,api,saved,statefile)
    elif a.action in ('pause','resume'):pause(a,api,saved,a.action=='pause')
    elif a.action=='status':print(json.dumps(remote(a,'status'),indent=2))
    else:wait(a,api,saved)


if __name__=='__main__':main()
