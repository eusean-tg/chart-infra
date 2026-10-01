#!/usr/bin/env python3
"""Per-profile Syncthing lifecycle; no source fetching, dependencies or app switch."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import access
import apps
import mongo
from common import STATE, guard, load, lock, run, save
from sources import valid_name
from sync_common import API, IGNORES, LEGACY_IGNORES, REPOS, fingerprint, manifest, ready

IMAGE = 'syncthing/syncthing:2.1.5@sha256:397aa00b92b48d65540ea3ae3cbf271b87bdccbe07a0b7bd7d2debc3a7b29138'


def location():
    return Path.home() / '.local/share/chart-infra/sync' / mongo.PROFILE


def inventory(allow_legacy=False):
    mongo.inventory()
    path = location()
    if path.resolve() != path:
        raise SystemExit('Symlinked sync identity path')
    inv = json.loads((path / 'inventory.json').read_text())
    for name in ('cert.pem', 'key.pem'):
        if hashlib.sha256((path / 'state' / name).read_bytes()).hexdigest() != inv['identity'][name]:
            raise SystemExit('Syncthing identity missing/replaced; refusing regeneration')
    for kind_name, uid in inv.get('volume_uids', {}).items():
        kind, name = kind_name.split('/', 1)
        obj = mongo.kget(kind, name, None if kind == 'pv' else 'profile')
        mongo.owned(obj)
        if not obj or obj['metadata']['uid'] != uid:
            raise SystemExit('Retained sync volume missing/replaced')
    for svc, folder in inv['folders'].items():
        root = Path(folder['host_path'])
        if root.resolve() != root or not root.is_dir() or not (root / '.stfolder').is_dir():
            raise SystemExit('Source mirror/marker missing or symlinked')
        lines=(root / '.stignore').read_text().splitlines()
        if lines != IGNORES and not (allow_legacy and lines==LEGACY_IGNORES):
            raise SystemExit('Mirror exclusions differ; review before syncing')
    return inv


def prepare(workspace, port):
    mongo.inventory()
    p = location()
    if p.exists():
        inv = inventory(allow_legacy=True)
        if inv['workspace'] != workspace or inv['port'] != port:
            raise SystemExit('Existing sync identity/selection differs; refusing implicit switch')
        for folder in inv['folders'].values():
            root=Path(folder['host_path'])
            if (root/'.stignore').read_text().splitlines()==LEGACY_IGNORES:
                if inv.get('peer') or {x.name for x in root.iterdir()}- {'.stfolder','.stignore'}:
                    raise SystemExit('Review ignore upgrade for this populated/paired folder explicitly')
                (root/'.stignore').write_text('\n'.join(IGNORES)+'\n')
        return inv
    mirror = mongo.BASE / 'source/mirrors' / workspace
    if p.resolve() != p or mirror.resolve() != mirror or mirror.exists():
        raise SystemExit('Refusing existing or symlinked mirror destination')
    with socket.socket() as s:
        s.bind((load('host.json')['tailscale'], port))
    for f in (Path.home() / '.local/share/chart-infra/sync').glob('*/inventory.json'):
        if json.loads(f.read_text())['port'] == port:
            raise SystemExit('Sync port reserved by another profile')
    p.mkdir(parents=True, mode=0o700)
    state = p / 'state'; state.mkdir(mode=0o700)
    gui_password=secrets.token_urlsafe(32)
    # Generate offline, then restrict the generated config before first serve.
    run(['docker', 'run', '-i', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
         '--user', f'{os.getuid()}:{os.getgid()}', '--entrypoint', '/bin/syncthing',
         '--mount', f'type=bind,src={state},dst=/var/syncthing', '--tmpfs', '/tmp',
         IMAGE, 'generate', '--home=/var/syncthing', '--no-port-probing',
         '--gui-user=chart-'+mongo.PROFILE, '--gui-password=-'], data=gui_password+'\n')
    (p/'gui-password').write_text(gui_password+'\n')
    tree = ET.parse(state / 'config.xml'); root = tree.getroot()
    for folder in root.findall('folder'): root.remove(folder)
    options = root.find('options')
    for key, value in {'globalAnnounceEnabled':'false', 'localAnnounceEnabled':'false',
                       'relaysEnabled':'false', 'natEnabled':'false', 'startBrowser':'false',
                       'urAccepted':'-1', 'crashReportingEnabled':'false', 'autoUpgradeIntervalH':'0'}.items():
        el = options.find(key)
        if el is None: el = ET.SubElement(options, key)
        el.text = value
    for el in options.findall('listenAddress'): options.remove(el)
    ET.SubElement(options, 'listenAddress').text = 'tcp://0.0.0.0:22000'
    gui = root.find('gui'); gui.find('address').text = '0.0.0.0:8384'
    # GUI remains cluster-internal, behind API-key auth and restrictive networking.
    tree.write(state / 'config.xml', encoding='utf-8', xml_declaration=True)
    device = root.find('device').attrib['id']
    inv = {'profile':mongo.PROFILE, 'workspace':workspace, 'port':port, 'image':IMAGE,
           'device_id':device, 'identity':{n:hashlib.sha256((state/n).read_bytes()).hexdigest() for n in ('cert.pem','key.pem')},
           'folders':{}, 'volume_uids':{}}
    for svc, repo in REPOS.items():
        dest = mirror / repo; dest.mkdir(parents=True, mode=0o700)
        (dest / '.stfolder').mkdir(); (dest / '.stignore').write_text('\n'.join(IGNORES)+'\n')
        inv['folders'][svc] = {'id':f'chart-{mongo.PROFILE}-{workspace}-{svc}', 'host_path':str(dest),
                               'pod_path':'/sources/'+svc, 'repo':repo, 'claim':'sync-'+svc+'-source'}
    mongo.write_json(p / 'inventory.json', inv)
    return inv


def manifests(inv):
    objects = apps.storage('sync-state', location() / 'state')
    vols = [{'name':'state','persistentVolumeClaim':{'claimName':'sync-state'}}, {'name':'tmp','emptyDir':{}}]
    mounts = [{'name':'state','mountPath':'/var/syncthing'}, {'name':'tmp','mountPath':'/tmp'}]
    for svc, f in inv['folders'].items():
        objects += apps.storage(f['claim'], Path(f['host_path']))
        vols.append({'name':svc,'persistentVolumeClaim':{'claimName':f['claim']}})
        mounts.append({'name':svc,'mountPath':f['pod_path']})
    podspec = {'automountServiceAccountToken':False,'securityContext':apps.podsecurity(),
               'containers':[{'name':'syncthing','image':IMAGE,'command':['/bin/syncthing','serve',
               '--home=/var/syncthing','--no-browser','--no-upgrade','--no-restart','--no-port-probing'],
               'env':[{'name':'HOME','value':'/tmp'}], 'securityContext':apps.containersecurity(),
               'volumeMounts':mounts,'readinessProbe':{'tcpSocket':{'port':8384},'periodSeconds':3}}], 'volumes':vols}
    objects += [mongo.object_('NetworkPolicy','chart-sync-isolation',{'podSelector':{'matchLabels':{'chart-runtime':'sync'}},
                'policyTypes':['Ingress','Egress'],'ingress':[],'egress':[]},api='networking.k8s.io/v1'),
                mongo.object_('Deployment','syncthing',{'replicas':1,'strategy':{'type':'Recreate'},
                'selector':{'matchLabels':{'chart-app':'syncthing'}},'template':{'metadata':{'labels':{'chart-app':'syncthing','chart-runtime':'sync'}},'spec':podspec}},api='apps/v1'),
                apps.service('syncthing',22000)]
    return objects


@contextlib.contextmanager
def client():
    inv = inventory()
    key = ET.parse(location() / 'state/config.xml').getroot().findtext('gui/apikey')
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    proc = subprocess.Popen(['kubectl','-n',mongo.NS,'port-forward','deployment/syncthing',f'{port}:8384','--address','127.0.0.1'],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    api = API(f'http://127.0.0.1:{port}', key)
    try:
        for _ in range(60):
            if proc.poll() is not None: raise SystemExit('Syncthing API forwarding failed')
            try:
                status = api.call('system/status')
                if status['myID'] != inv['device_id']: raise SystemExit('Unexpected Syncthing device identity')
                break
            except (OSError, RuntimeError): time.sleep(0.25)
        else: raise SystemExit('Syncthing API not ready')
        yield api
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill();proc.wait()


def up():
    inv = inventory(); objects = manifests(inv)
    # NetworkPolicies are additive: exclude this Pod from the older namespace-wide allow.
    mongo.apply_owned([o for o in mongo.manifests(mongo.inventory()) if o['kind']=='NetworkPolicy'])
    mongo.apply_owned([o for o in objects if o['kind']=='NetworkPolicy'])
    mongo.apply_owned([o for o in objects if o['kind']!='NetworkPolicy'])
    for obj in objects:
        if obj['kind'] in ('PersistentVolume','PersistentVolumeClaim'):
            kind = 'pv' if obj['kind']=='PersistentVolume' else 'pvc';name=obj['metadata']['name']
            inv['volume_uids'][kind+'/'+name]=mongo.kget(kind,name,None if kind=='pv' else 'profile')['metadata']['uid']
    mongo.write_json(location() / 'inventory.json', inv)
    run(['kubectl','-n',mongo.NS,'rollout','status','deployment/syncthing','--timeout=120s'])
    with client() as api:
        existing = {f['id']:f for f in api.call('config/folders')}
        for f in inv['folders'].values():
            if f['id'] in existing:
                old = existing[f['id']]
                if old['path']!=f['pod_path'] or old['type']!='receiveonly': raise SystemExit('Unexpected managed folder configuration')
                continue
            obj=api.call('config/defaults/folder');obj.update({'id':f['id'],'label':f['id'],'path':f['pod_path'],
                'type':'receiveonly','paused':True,'devices':[{'deviceID':inv['device_id']}],
                'rescanIntervalS':300,'fsWatcherEnabled':True,'ignorePerms':True,'ignoreDelete':False,
                'filesystemType':'basic','versioning':{'type':'','params':{}},'maxConflicts':-1})
            api.call('config/folders','POST',obj)
    address=mongo.kget('service','syncthing')['spec']['clusterIP']
    access.set_route('sync-'+mongo.PROFILE,{'port':inv['port'],'address':address,'target_port':22000})
    save(f'profiles/{mongo.PROFILE}/sync-manifest.json',objects)
    print('Syncthing ready; pairing/folders remain paused until explicit laptop setup.')


def down(data_only=False):
    inv=inventory();access.set_route('sync-'+mongo.PROFILE,None)
    obj=mongo.kget('deployment','syncthing');mongo.owned(obj)
    if obj:
        run(['kubectl','-n',mongo.NS,'scale','deployment/syncthing','--replicas=0'])
        run(['kubectl','-n',mongo.NS,'wait','--for=delete','pod','-l','chart-app=syncthing','--timeout=90s'])
    if data_only:
        for o in manifests(inv):
            if o['kind'] in ('Deployment','Service','NetworkPolicy'):
                mongo.owned(mongo.kget(o['kind'],o['metadata']['name']))
                run(['kubectl','-n',mongo.NS,'delete',o['kind'],o['metadata']['name'],'--ignore-not-found'])
    print('Sync stopped; device identity, pairing, mirrors and all volumes retained.')


def export():
    inv=inventory()
    return {'profile':mongo.PROFILE,'workspace':inv['workspace'],'device_id':inv['device_id'],
            'address':f'tcp://{load("host.json")["tailscale"]}:{inv["port"]}',
            'folders':{s:{'id':f['id'],'repo':f['repo']} for s,f in inv['folders'].items()}}


def pair(payload):
    inv=inventory();device=payload['device_id']
    if not re.fullmatch(r'[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}',device) or device==inv['device_id']:
        raise SystemExit('Invalid laptop device identity')
    if inv.get('peer') not in (None,device): raise SystemExit('Profile already paired to a different device')
    if set(payload['paths'])!=set(REPOS): raise SystemExit('All three repository paths must be provided')
    if inv.get('laptop_paths') not in (None,payload['paths']): raise SystemExit('Existing laptop paths differ; refusing reassignment')
    with client() as api:
        devices={d['deviceID']:d for d in api.call('config/devices')}
        if device in devices and inv.get('peer')!=device: raise SystemExit('Unowned existing device; refusing adoption')
        # Inventory first: interrupted setup can reconcile only this relationship.
        inv.update({'peer':device,'laptop_paths':payload['paths']});mongo.write_json(location()/'inventory.json',inv)
        if device not in devices:
            obj=api.call('config/defaults/device');obj.update({'deviceID':device,'name':'chart-laptop-'+mongo.PROFILE,
                 'addresses':['dynamic'],'introducer':False,'autoAcceptFolders':False,'paused':False})
            api.call('config/devices','POST',obj)
        for f in inv['folders'].values():
            obj=api.call('config/folders/'+f['id'])
            peers={d['deviceID'] for d in obj['devices']}
            if peers-{inv['device_id'],device}: raise SystemExit('Unexpected third-party folder share')
            obj.update({'devices':[{'deviceID':inv['device_id']},{'deviceID':device}],'paused':True})
            api.call('config/folders/'+f['id'],'PUT',obj)
    return export()


def pause(paused):
    inv=inventory()
    if not paused and not inv.get('peer'): raise SystemExit('Pair the laptop before resuming')
    with client() as api:
        for f in inv['folders'].values():api.call('config/folders/'+f['id'],'PATCH',{'paused':paused})
    return {'paused':paused}


def status(scan=False):
    inv=inventory();out={'peer':inv.get('peer'),'folders':{}}
    with client() as api:
        connections=api.call('system/connections')['connections']
        out['connected']=connections.get(inv.get('peer'),{}).get('connected',False)
        for svc,f in inv['folders'].items():
            cfg=api.call('config/folders/'+f['id'])
            if cfg['paused']:
                out['folders'][svc]={'paused':True,'ready':False};continue
            if scan:api.call('db/scan','POST',folder=f['id'])
            stats=api.call('db/status',folder=f['id'])
            out['folders'][svc]={'paused':False,'ready':ready(stats),'state':stats['state'],
                'needed':stats.get('needTotalItems',0),'errors':stats.get('pullErrors',0),
                'local_changes':stats.get('receiveOnlyTotalItems',0)}
    return out


def checkpoint(payload):
    inv=inventory();st=status(scan=True)
    if not st['connected'] or not all(f['ready'] for f in st['folders'].values()):
        raise SystemExit('Sync disconnected, incomplete, conflicted or paused')
    if payload['device_id']!=inv['peer'] or set(payload['repos'])!=set(REPOS):raise SystemExit('Unexpected checkpoint owner/repositories')
    sources={};result={}
    for svc,f in inv['folders'].items():
        files=manifest(f['host_path']);actual=fingerprint(files);expected=payload['repos'][svc]
        if actual!=expected['fingerprint']:raise SystemExit(f'{svc}: source fingerprint differs; wait for sync and retry')
        if not {'package.json','pnpm-lock.yaml','src/index.ts'}<=set(files):raise SystemExit('Incomplete backend source')
        result[svc]={'fingerprint':actual,'files':len(files),'head':expected.get('head'),'dirty':expected.get('dirty')}
        sources[f['repo']]={'path':f['host_path'],'revision':expected.get('head'),'dirty':expected.get('dirty'),
                            'sync':True,'fingerprint':actual,'claim':f['claim']}
    save(f'sources/{mongo.PROFILE}/{inv["workspace"]}.json',sources)
    save(f'profiles/{mongo.PROFILE}/sync-checkpoint.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','up','down','export','pair','pause','resume','status','scan','checkpoint'])
    p.add_argument('--profile',type=valid_name,default='sean');p.add_argument('--workspace',type=valid_name,default='laptop')
    p.add_argument('--port',type=int,default=22001);p.add_argument('--data-only',action='store_true')
    a=p.parse_args()
    if a.data_only and a.action!='down':p.error('--data-only is for down only')
    if not 1024<=a.port<=65535:p.error('Invalid port')
    guard();held=lock('chart-sync-'+a.profile);apps.setup(a.profile)
    if a.action=='prepare':
        reservation=lock('chart-profiles');prepare(a.workspace,a.port);print('Prepared private sync identity and empty mirrors.')
    elif a.action=='up':up()
    elif a.action=='down':down(a.data_only)
    else:
        if a.action=='export':result=export()
        elif a.action=='pair':result=pair(json.load(sys.stdin))
        elif a.action=='checkpoint':result=checkpoint(json.load(sys.stdin))
        elif a.action in ('pause','resume'):result=pause(a.action=='pause')
        else:result=status(a.action=='scan')
        print(json.dumps(result,indent=2))

if __name__=='__main__':main()
