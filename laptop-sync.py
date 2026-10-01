#!/usr/bin/env python3
"""Agent building blocks for owned Mutagen sessions; edits and Git stay on the laptop."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from sync_common import (REPOS,VERSION,check_roots,config_text,configuration,policy_hash,
                         manifest,fingerprint,write_json,mutagen,sessions)


def remote(args,action,payload=None):
    cmd=shlex.join(['python3',str(Path(args.remote_dir)/'sync.py'),action,'--profile',args.profile])
    result=subprocess.check_output(['ssh','-o','BatchMode=yes',args.host,cmd],
        input=None if payload is None else json.dumps(payload),text=True,timeout=args.timeout)
    return json.loads(result)


def endpoint(args,path):return args.host+':'+path


def session_name(args,svc):return 'chart-'+args.profile+'-'+svc


def validate_session(args,record,svc,state):
    f=state['remote']['folders'][svc];user,host=args.host.split('@',1)
    if record.get('labels',{}).get('chart-owner')!=state['client_id'] or record.get('name')!=session_name(args,svc):
        raise SystemExit('Mutagen ownership differs; refusing adoption')
    a,b=record['alpha'],record['beta']
    if a['protocol']!='local' or a['path']!=state['paths'][svc] or b['protocol']!='ssh' or b.get('user')!=user or b.get('host')!=host or b['path']!=f['host_path']:
        raise SystemExit('Mutagen endpoint differs from registered mapping')
    for key,value in configuration().items():
        if record.get(key)!=value:raise SystemExit('Mutagen session policy differs: '+key)
    if b.get('stageMode')!='neighboring':raise SystemExit('Remote staging must remain on HDD')
    if record.get('creatingVersion')!=VERSION:raise SystemExit('Session version differs; review migration')
    return record


def owned_sessions(args,state):
    all_sessions=sessions(args.mutagen);owned={}
    for svc in REPOS:
        sid=state.get('sessions',{}).get(svc)
        found=[r for r in all_sessions if r['identifier']==sid]
        if len(found)!=1:raise SystemExit(svc+': owned session missing; inspect instead of recreating')
        owned[svc]=validate_session(args,found[0],svc,state)
    return owned


def syncthing_roots(args):
    candidates=[Path(p).expanduser() for p in (args.syncthing_config or [])]
    if not candidates:
        candidates=[Path.home()/'Library/Application Support/Syncthing/config.xml',
                    Path.home()/'.local/state/syncthing/config.xml',Path.home()/'.config/syncthing/config.xml']
    result=[]
    for p in candidates:
        if p.exists():
            for f in ET.parse(p).getroot().findall('folder'):
                if f.get('path'):result.append(('Syncthing '+f.get('id','folder'),f.get('path')))
    return result


def plan(args,saved):
    exp=remote(args,'export')
    if exp['transport']!='mutagen-ssh' or exp['policy_hash']!=policy_hash() or exp['version']!=VERSION:
        raise SystemExit('PC helper/policy version differs; copy the matching helper files')
    if args.host.split('@',1)[0]!=exp['owner']:raise SystemExit('SSH account must match registered mirror owner')
    if saved and (saved['host']!=args.host or saved['remote_dir']!=args.remote_dir or saved['remote']!=exp):
        raise SystemExit('PC registration/path changed; explicit reassignment required')
    supplied={s:getattr(args,s) for s in REPOS}
    if not any(supplied.values()) and saved:supplied=saved['paths']
    if not all(supplied.values()):raise SystemExit('Supply all three explicit repo paths; no workspace convention is assumed')
    existing=syncthing_roots(args);records=sessions(args.mutagen)
    owner=saved.get('client_id') if saved else None
    for r in records:
        if owner and r.get('labels',{}).get('chart-owner')==owner:continue
        if r.get('name') in [session_name(args,s) for s in REPOS]:raise SystemExit('Unowned session name collision')
        for side in ('alpha','beta'):
            e=r[side]
            if e['protocol']=='local':existing.append(('Mutagen '+r['identifier'],e['path']))
            elif e['protocol']=='ssh' and (e.get('user','')+'@'+e.get('host',''))==args.host:
                q=Path(e['path'])
                for f in exp['folders'].values():
                    p=Path(f['host_path'])
                    if p==q or p in q.parents or q in p.parents:raise SystemExit('Remote target overlaps another Mutagen session')
    paths=check_roots(supplied,existing)
    if saved and saved['paths']!=paths:raise SystemExit('Laptop paths changed; refusing implicit relocation')
    for path in paths.values():manifest(path)
    state=saved or {'host':args.host,'remote_dir':args.remote_dir,'remote':exp,'paths':paths,'sessions':{}}
    for r in records:
        if owner and r.get('labels',{}).get('chart-owner')==owner:
            svcs=[s for s in REPOS if session_name(args,s)==r.get('name')]
            if len(svcs)!=1:raise SystemExit('Unexpected session carrying this ownership label')
            validate_session(args,r,svcs[0],state)
    return state


def setup(args,saved,path):
    state=plan(args,saved)
    if not saved:
        state['client_id']=secrets.token_hex(24);state['phase']='creating-paused';write_json(path,state)
    config=path.parent/'mutagen-sync.yml';text=config_text()
    if config.exists() and config.read_text()!=text:raise SystemExit('Local session configuration changed; review before setup')
    config.write_text(text)
    for svc in REPOS:
        records=sessions(args.mutagen)
        found=[r for r in records if r.get('name')==session_name(args,svc)]
        if len(found)>1:raise SystemExit('Duplicate session name')
        if not found:
            if svc in state['sessions']:raise SystemExit('Recorded session disappeared; no automatic recreation')
            # Persisted owner label permits recovery if interrupted after create but before saving its ID.
            print(mutagen(args.mutagen,'sync','create','--paused','--no-global-configuration',
                          '-c',str(config),'--stage-mode-beta','neighboring',
                          '--name',session_name(args,svc),'--label','chart-profile='+args.profile,
                          '--label','chart-owner='+state['client_id'],state['paths'][svc],
                          endpoint(args,state['remote']['folders'][svc]['host_path']),timeout=args.timeout).strip())
            found=[r for r in sessions(args.mutagen) if r.get('name')==session_name(args,svc)]
        if len(found)!=1:raise SystemExit('Unable to identify newly created session')
        record=validate_session(args,found[0],svc,state)
        if svc in state['sessions'] and state['sessions'][svc]!=record['identifier']:raise SystemExit('Session identity replaced')
        state['sessions'][svc]=record['identifier'];write_json(path,state)
    remote(args,'register',{'registration':state['remote']['registration'],'policy_hash':policy_hash(),
                            'client_id':state['client_id'],'sessions':state['sessions'],'paths':state['paths']})
    state['phase']='registered';write_json(path,state)
    print('Registered owned sessions. Newly created sessions are paused; setup leaves existing sessions unchanged.')


def invalidation(args,state):
    remote(args,'invalidate',{'registration':state['remote']['registration'],'client_id':state['client_id']})


def change(args,state,action):
    owned_sessions(args,state)
    invalidation(args,state) # Clear activation attestation before any resume (including interrupted ones).
    print(mutagen(args.mutagen,'sync',action,*state['sessions'].values(),timeout=args.timeout).strip())


def clean(records,paused=False):
    for r in records.values():
        if bool(r.get('paused',False))!=paused:return False
        if r.get('conflicts') or r.get('excludedConflicts') or r.get('lastError'):return False
        for side in ('alpha','beta'):
            e=r[side]
            if e.get('scanProblems') or e.get('transitionProblems') or e.get('excludedScanProblems') or e.get('excludedTransitionProblems'):return False
            if not paused and not e.get('connected'):return False
        if not paused and r.get('status')!='watching':return False
    return True


def snapshot(state):
    out={}
    for svc,path in state['paths'].items():
        files=manifest(path)
        head=subprocess.check_output(['git','-C',path,'rev-parse','HEAD'],text=True).strip()
        dirty=bool(subprocess.check_output(['git','-C',path,'status','--porcelain'],text=True))
        out[svc]={'fingerprint':fingerprint(files),'head':head,'dirty':dirty}
    return out


def checkpoint(args,state,freeze):
    records=owned_sessions(args,state)
    if any(r.get('paused') for r in records.values()):raise SystemExit('Resume all owned sessions before wait/freeze')
    before=snapshot(state)
    print(mutagen(args.mutagen,'sync','flush',*state['sessions'].values(),timeout=args.timeout).strip(),file=sys.stderr)
    deadline=time.monotonic()+args.timeout
    while time.monotonic()<deadline:
        records=owned_sessions(args,state)
        if clean(records):break
        if any(r.get('conflicts') or r.get('lastError') for r in records.values()):
            raise SystemExit('Sync conflicts/errors; inspect status, never automatically reset/overwrite')
        time.sleep(0.5)
    else:raise SystemExit('Sync did not reach clean watching state')
    if freeze:
        invalidation(args,state)
        mutagen(args.mutagen,'sync','pause',*state['sessions'].values(),timeout=args.timeout)
        if not clean(owned_sessions(args,state),paused=True):raise SystemExit('Sessions not cleanly paused')
    after=snapshot(state)
    if before!=after:raise SystemExit('Laptop source changed during verification; stop editing, resume if paused, retry')
    result=remote(args,'checkpoint',{'registration':state['remote']['registration'],'policy_hash':policy_hash(),
        'client_id':state['client_id'],'sessions':state['sessions'],'paused':freeze,'repos':after})
    print(json.dumps({'paused':freeze,'repos':result},indent=2))


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','setup','resume','pause','status','wait','freeze'])
    p.add_argument('--host',required=True,help='Explicit user@SSH-host (SSH aliases supported)')
    p.add_argument('--remote-dir',required=True);p.add_argument('--profile',required=True)
    p.add_argument('--mutagen',default='mutagen');p.add_argument('--timeout',type=int,default=180)
    p.add_argument('--syncthing-config',action='append',help='Additional local Syncthing config.xml to check for root overlap; never modified')
    for svc in REPOS:p.add_argument('--'+svc,help='Explicit absolute local checkout path')
    a=p.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',a.profile):p.error('Invalid profile')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9.-]*',a.host):p.error('Use explicit user@host or user@SSH-alias')
    if not Path(a.remote_dir).is_absolute():p.error('Remote chart-infra directory must be absolute')
    if mutagen(a.mutagen,'version').strip()!=VERSION:p.error('Use verified Mutagen '+VERSION+' for this runbook')
    root=Path.home()/'.local/state/chart-infra/mutagen'/a.profile
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    held=(root/'setup.lock').open('a');fcntl.flock(held,fcntl.LOCK_EX)
    path=root/'sessions.json';saved=json.loads(path.read_text()) if path.exists() else None
    if a.action=='plan':print(json.dumps(plan(a,saved),indent=2));return
    if a.action=='setup':setup(a,saved,path);return
    if not saved or saved.get('phase')!='registered':raise SystemExit('Complete plan/setup first')
    plan(a,saved)
    if a.action=='status':print(json.dumps({'sessions':owned_sessions(a,saved),'pc':remote(a,'status')},indent=2))
    elif a.action in ('resume','pause'):change(a,saved,a.action)
    else:checkpoint(a,saved,a.action=='freeze')

if __name__=='__main__':main()
