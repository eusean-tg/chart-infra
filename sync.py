#!/usr/bin/env python3
"""HDD source mirrors and verified checkpoints for laptop-owned Mutagen over SSH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import sys
import time

import apps
import mongo
from common import guard,load,lock,save
from sources import valid_name
from sync_common import REPOS,POLICY,VERSION,policy_hash,manifest,fingerprint

LEGACY_POLICIES = {
    'chart-mutagen-v1':'f096daf19e0ad5a9112ea62041f23c8cd227a74207c1f42da5197fb39bb721ef',
}

def statepath():return mongo.IDENTITY/'source-sync.json'


def verify_volumes(inv):
    for key,uid in inv['volume_uids'].items():
        kind,name=key.split('/',1);obj=mongo.kget(kind,name,None if kind=='pv' else 'profile');mongo.owned(obj)
        if not obj or obj['metadata']['uid']!=uid:raise SystemExit('Retained source-sync volume missing/replaced')


def inventory(allow_legacy_policy=False):
    mongo.inventory()
    inv=json.loads(statepath().read_text())
    current=inv.get('policy')==POLICY and inv.get('policy_hash')==policy_hash()
    legacy=allow_legacy_policy and LEGACY_POLICIES.get(inv.get('policy'))==inv.get('policy_hash') and inv.get('policy') in LEGACY_POLICIES
    if inv['profile']!=mongo.PROFILE or inv['transport']!='mutagen-ssh' or not (current or legacy):
        raise SystemExit('Source-sync identity or policy differs; explicit migration required')
    if inv['uid']!=os.getuid():raise SystemExit('Run source-sync operations as the registered profile owner')
    old=mongo.kget('deployment','syncthing');mongo.owned(old)
    if old and (old['spec'].get('replicas',0) or old.get('status',{}).get('replicas',0)):
        raise SystemExit('Legacy Syncthing still running: two engines must never write the same mirror')
    verify_volumes(inv)
    for f in inv['folders'].values():
        p=Path(f['host_path']);marker=p/'.chart-sync-root'
        if p.resolve()!=p or not p.is_dir() or p.stat().st_uid!=inv['uid']:
            raise SystemExit('Source root missing/replaced/symlinked or wrong owner')
        if marker.is_symlink() or marker.read_text()!=f['marker']:
            raise SystemExit('Source identity marker missing/replaced')
    return inv


def upgrade_policy():
    """Reviewed v1 -> v2 migration for an unused registration; preserve identity."""
    inv=inventory(allow_legacy_policy=True)
    if inv['policy']==POLICY and inv['policy_hash']==policy_hash():
        return {'policy':POLICY,'policy_hash':policy_hash(),'changed':False}
    if LEGACY_POLICIES.get(inv['policy'])!=inv['policy_hash']:
        raise SystemExit('Unknown policy; refusing automatic migration')
    if any(k in inv for k in ('client_id','sessions','paths','checkpoint','checkpoint_at')) or inv.get('frozen'):
        raise SystemExit('Registered/checkpointed source requires a coordinated laptop session migration')
    if any(manifest(f['host_path']) for f in inv['folders'].values()):
        raise SystemExit('Populated mirrors require explicit source/session migration review')
    receipt=save(f'profiles/{mongo.PROFILE}/policy-upgrades/before-{time.time_ns()}.json',inv)
    updated={**inv,'policy':POLICY,'policy_hash':policy_hash()}
    mongo.write_json(statepath(),updated)
    inventory()
    return {'policy':POLICY,'policy_hash':policy_hash(),'changed':True,'previous_inventory':str(receipt)}


def prepare(workspace,adopt=False):
    mongo.inventory()
    if statepath().exists():
        inv=inventory()
        if inv['workspace']!=workspace:raise SystemExit('Existing workspace differs; refusing implicit reassignment')
        print('Retained Mutagen mirror registration reused.');return
    if mongo.PROFILE!=pwd.getpwuid(os.getuid()).pw_name:
        raise SystemExit('Provision the developer Linux account/scoped access first; prepare as that profile owner')
    legacy=Path.home()/'.local/share/chart-infra/sync'/mongo.PROFILE/'inventory.json'
    inv={'transport':'mutagen-ssh','profile':mongo.PROFILE,'workspace':workspace,
         'uid':os.getuid(),'gid':os.getgid(),'owner':pwd.getpwuid(os.getuid()).pw_name,
         'registration':secrets.token_hex(24),'policy':POLICY,'policy_hash':policy_hash(),
         'version':VERSION,'folders':{},'volume_uids':{},'frozen':False}
    if legacy.exists():
        if not adopt:raise SystemExit('Existing Syncthing state: use explicit --adopt-syncthing migration')
        import syncthing_legacy as old
        prior=old.inventory()
        if prior['workspace']!=workspace or prior.get('peer'):raise SystemExit('Paired/different legacy workspace requires separate migration review')
        for f in prior['folders'].values():
            p=Path(f['host_path'])
            if {x.name for x in p.iterdir()}-{'.stfolder','.stignore','node_modules'} or (p/'node_modules').exists() and any((p/'node_modules').iterdir()):
                raise SystemExit('Populated legacy mirror: inspect before adoption')
        save(f'profiles/{mongo.PROFILE}/syncthing-before-mutagen.json',prior)
        old.down(data_only=True)
        inv['volume_uids']=prior['volume_uids'].copy()
        inv['legacy_identity_retained']=str(legacy)
        inv['folders']={s:{'repo':f['repo'],'host_path':f['host_path'],'claim':f['claim']} for s,f in prior['folders'].items()}
    else:
        if adopt:raise SystemExit('No legacy Syncthing inventory to adopt')
        base=mongo.BASE/'source/mirrors'/workspace
        if base.exists() or base.resolve()!=base:raise SystemExit('Refusing existing or symlinked mirror destination')
        for svc,repo in REPOS.items():
            dest=base/repo;dest.mkdir(parents=True,mode=0o700)
            inv['folders'][svc]={'repo':repo,'host_path':str(dest),'claim':'source-'+workspace+'-'+svc}
    for svc,f in inv['folders'].items():
        root=Path(f['host_path']);f['marker']=secrets.token_hex(24)+'\n'
        marker=root/'.chart-sync-root'
        if marker.exists():raise SystemExit('Existing unregistered source marker; inspect interrupted setup')
        marker.write_text(f['marker']);(root/'node_modules').mkdir(exist_ok=True)
        objects=apps.storage(f['claim'],root);mongo.apply_owned(objects)
        for obj in objects:
            kind='pv' if obj['kind']=='PersistentVolume' else 'pvc';name=obj['metadata']['name']
            inv['volume_uids'][kind+'/'+name]=mongo.kget(kind,name,None if kind=='pv' else 'profile')['metadata']['uid']
    mongo.write_json(statepath(),inv)
    inventory()
    print('Mutagen mirrors registered. No laptop sessions created; apps/source selection unchanged. All old state/volumes retained.')


def export():
    inv=inventory()
    return {k:inv[k] for k in ('transport','profile','workspace','uid','gid','owner','registration','policy','policy_hash','version')} | {
        'folders':{s:{k:f[k] for k in ('repo','host_path','claim')} for s,f in inv['folders'].items()}}


def validate_payload(inv,payload):
    if payload.get('registration')!=inv['registration'] or payload.get('policy_hash')!=policy_hash():
        raise SystemExit('Unexpected source registration/policy')
    if set(payload.get('repos',{}))!=set(REPOS):raise SystemExit('All three repositories are required')
    if payload.get('client_id')!=inv.get('client_id') or not inv.get('client_id'):raise SystemExit('Unexpected laptop owner')
    if payload.get('sessions')!=inv.get('sessions') or not inv.get('sessions'):raise SystemExit('Unexpected Mutagen sessions')


def register(payload):
    inv=inventory()
    if payload.get('registration')!=inv['registration'] or payload.get('policy_hash')!=policy_hash():raise SystemExit('Registration/policy mismatch')
    client=payload.get('client_id','');paths=payload.get('paths',{});sessions=payload.get('sessions',{})
    if not re.fullmatch(r'[0-9a-f]{48}',client) or set(paths)!=set(REPOS) or set(sessions)!=set(REPOS):raise SystemExit('Invalid registration')
    if any(not isinstance(p,str) or not p.startswith('/') for p in paths.values()):raise SystemExit('Explicit absolute laptop paths required')
    if len(set(sessions.values()))!=3 or any(not re.fullmatch(r'sync_[A-Za-z0-9]+',s) for s in sessions.values()):raise SystemExit('Invalid session identities')
    unchanged = inv.get('client_id')==client and inv.get('paths')==paths and inv.get('sessions')==sessions
    for key,value in [('client_id',client),('paths',paths),('sessions',sessions)]:
        if key in inv and inv[key]!=value:raise SystemExit('Existing laptop/session mapping differs; explicit reassignment required')
        inv[key]=value
    if not unchanged:
        inv['frozen']=False;mongo.write_json(statepath(),inv)
    return {'registered':True}


def checkpoint(payload):
    inv=inventory();validate_payload(inv,payload)
    if type(payload.get('paused')) is not bool:raise SystemExit('Explicit session pause attestation required')
    results={};sources={}
    for svc,f in inv['folders'].items():
        files=manifest(f['host_path']);actual=fingerprint(files);expected=payload['repos'][svc]
        if actual!=expected['fingerprint']:raise SystemExit(svc+': fingerprint differs; flush, inspect conflicts and retry')
        if not {'package.json','pnpm-lock.yaml','src/index.ts'}<=set(files):raise SystemExit('Incomplete source')
        results[svc]={'fingerprint':actual,'files':len(files),'head':expected.get('head'),'dirty':expected.get('dirty')}
        sources[f['repo']]={'path':f['host_path'],'revision':expected.get('head'),'dirty':expected.get('dirty'),
                           'sync':True,'transport':'mutagen-ssh','fingerprint':actual,'claim':f['claim']}
    # Repeat to detect edits during the three-repo snapshot. No filesystem snapshot is implied.
    if any(fingerprint(manifest(f['host_path']))!=results[s]['fingerprint'] for s,f in inv['folders'].items()):
        raise SystemExit('Sources changed during checkpoint; pause editing and retry')
    inv.update({'frozen':payload['paused'],'checkpoint':results,'checkpoint_at':time.time()})
    save(f'sources/{mongo.PROFILE}/{inv["workspace"]}.json',sources)
    mongo.write_json(statepath(),inv)
    save(f'profiles/{mongo.PROFILE}/sync-checkpoint.json',{'transport':'mutagen-ssh','paused':payload['paused'],'repos':results})
    return results


def invalidate(payload):
    inv=inventory()
    if payload.get('client_id')!=inv.get('client_id') or payload.get('registration')!=inv['registration']:
        raise SystemExit('Unexpected source owner')
    inv['frozen']=False;mongo.write_json(statepath(),inv)
    return {'frozen':False}


def assert_selection(workspace,sources):
    inv=inventory()
    if inv['workspace']!=workspace or not inv.get('frozen') or not inv.get('checkpoint'):
        raise SystemExit('Run laptop freeze to flush, pause all owned sessions and record a matching checkpoint before selection/install')
    for svc,f in inv['folders'].items():
        src=sources[f['repo']]
        if src['path']!=f['host_path'] or src.get('claim')!=f['claim'] or src.get('fingerprint')!=inv['checkpoint'][svc]['fingerprint']:
            raise SystemExit('Source selection differs from registered checkpoint')
        if fingerprint(manifest(f['host_path']))!=src['fingerprint']:raise SystemExit('Source changed after checkpoint; freeze again')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','upgrade-policy','export','status','register','checkpoint','invalidate'])
    p.add_argument('--profile',type=valid_name,default='sean');p.add_argument('--workspace',type=valid_name,default='laptop')
    p.add_argument('--adopt-syncthing',action='store_true')
    a=p.parse_args()
    if a.adopt_syncthing and a.action!='prepare':p.error('--adopt-syncthing is only for prepare')
    read_only=a.action in ('export','status')
    # Reads still verify cluster/HDD/volume ownership. They do not mutate a
    # selection, so must not wait behind installs or rollout lifecycle locks.
    # Inventory JSON uses atomic replacement; concurrent preparation may fail
    # a read's identity checks and should be retried, never repaired by a read.
    guard(check_capacity=not read_only)
    if not read_only:
        held=lock('chart-apps-'+a.profile)
        mh=lock('chart-mongo-'+a.profile)
        sh=lock('chart-sync-'+a.profile)
    mongo.configure(a.profile)
    if a.action=='prepare':prepare(a.workspace,a.adopt_syncthing);return
    if a.action=='upgrade-policy':result=upgrade_policy()
    elif a.action=='export':result=export()
    elif a.action=='status':
        inv=inventory();result={'transport':inv['transport'],'workspace':inv['workspace'],'registered':bool(inv.get('client_id')),
                              'frozen_attested_by_laptop':inv['frozen'],'checkpoint_at':inv.get('checkpoint_at'),
                              'note':'Session connectivity/conflicts must be queried on the laptop; this PC does not run its daemon.'}
    else:result=globals()[a.action](json.load(sys.stdin))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
