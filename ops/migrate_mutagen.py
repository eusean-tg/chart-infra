#!/usr/bin/env python3
"""Sean migration evidence: retain app/data/volume identities and unrelated OpenScape."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import guard,save,load,run
import apps,mongo

def snapshot():
 mongo.configure('sean');mongo.inventory();inv=apps.record()
 pods=json.loads(run(['kubectl','-n',mongo.NS,'get','pods','-o','json'],capture=True))['items']
 apppods={p['metadata']['labels'].get('chart-app',p['metadata']['name']):p['metadata']['uid'] for p in pods if p['metadata']['name']=='mongo-0' or p['metadata']['labels'].get('chart-app') in ('auth','tharamine','orange','redis')}
 osc=json.loads(run(['kubectl','-n','openscape-sync','get','deployment','-o','json'],capture=True))
 return {'apps':inv,'pods':apppods,
  'keys':{str(p.relative_to(mongo.IDENTITY)):hashlib.sha256(p.read_bytes()).hexdigest() for p in mongo.IDENTITY.rglob('*') if p.is_file() and p.name!='source-sync.json'},
  'openscape_specs':[d['spec'] for d in osc['items']],
  'openscape_config':hashlib.sha256((Path.home()/'services/openscape-sync/state/config.xml').read_bytes()).hexdigest()}

def main():
 guard();name='profiles/sean/mutagen-migration-before.json'
 if sys.argv[1]=='before':save(name,snapshot());print('Recorded retained state and unrelated deployment fingerprints.');return
 before=load(name);after=snapshot();assert before==after,'Existing app/data identities/config or OpenScape changed'
 import sync
 inv=sync.inventory();assert not mongo.kget('deployment','syncthing') and not mongo.kget('service','syncthing')
 assert not mongo.kget('networkpolicy','chart-sync-isolation')
 legacy=json.loads((Path.home()/'.local/share/chart-infra/sync/sean/inventory.json').read_text())
 for name,digest in legacy['identity'].items():
  assert hashlib.sha256((Path.home()/'.local/share/chart-infra/sync/sean/state'/name).read_bytes()).hexdigest()==digest
 assert inv['volume_uids']==legacy['volume_uids']
 ip=load('host.json')['tailscale']
 with socket.socket() as sock:
  sock.settimeout(3);assert sock.connect_ex((ip,22001))!=0,'Old listener remains'
 health=json.load(urllib.request.urlopen('http://'+ip+':13000/api/v1/health/services',timeout=10))
 assert health['status']=='ok'
 result={'passed':True,'legacy_runtime_removed':True,'listener_22001_withdrawn':True,
         'all_legacy_volumes_and_device_identity_retained':True,'apps_pod_uids_and_selection_unchanged':True,
         'mongo_pod_and_identity_files_unchanged':True,'openscape_unchanged':True,'api_health':health,
         'mac_sessions_registered':bool(inv.get('client_id'))}
 save('profiles/sean/mutagen-migration.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':main()
