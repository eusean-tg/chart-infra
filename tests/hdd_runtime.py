#!/usr/bin/env python3
"""Activate Sean's verified HDD copies; verify persistent identities/data and health."""
import hashlib
import argparse
import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import apps,mongo
from common import guard,lock,load,save


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['switch','check'],default='switch',nargs='?');args=p.parse_args()
    guard();a=lock('chart-apps-sean');m=lock('chart-mongo-sean');apps.setup('sean')
    old=apps.record();before=mongo.inventory()
    for svc,(repo,_,_) in apps.SERVICES.items():
        assert load('sources/sean/pilot.json')[repo]['path'].startswith('/mnt/hdd/')
        assert load('dependencies/sean/pilot/'+repo+'.json')['modules'].startswith('/mnt/hdd/')
    identity={str(p.relative_to(mongo.IDENTITY)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in mongo.IDENTITY.rglob('*') if p.is_file() and p.name not in ('inventory.json',)
              and not p.name.startswith('selection-before-')}
    if args.action=='switch':
        apps.down()
        workspaces=mongo.shell('print(EJSON.stringify(db.getSiblingDB("orange").workspaces.find().sort({_id:1}).toArray()))')
        save('profiles/sean/hdd-selection-before.json',{'inventory':old,'workspace_sha256':hashlib.sha256(workspaces.encode()).hexdigest(),'identity':identity})
        apps.select('pilot');apps.up()
    prior=load('profiles/sean/hdd-selection-before.json');old=prior['inventory'];identity=prior['identity'];current=apps.record()
    assert all(current['volume_uids'][k]==v for k,v in old['volume_uids'].items())
    after=mongo.inventory();assert before['pv_uid']==after['pv_uid'] and before['pvc_uid']==after['pvc_uid']
    for name,digest in identity.items():assert hashlib.sha256((mongo.IDENTITY/name).read_bytes()).hexdigest()==digest
    workspaces=mongo.shell('print(EJSON.stringify(db.getSiblingDB("orange").workspaces.find().sort({_id:1}).toArray()))')
    assert hashlib.sha256(workspaces.encode()).hexdigest()==prior['workspace_sha256']
    health=json.load(urllib.request.urlopen('http://100.66.127.115:13000/api/v1/health/services',timeout=15))
    assert all(v['db']=='up' for v in health['services'].values())
    save('profiles/sean/hdd-selection.json',{'passed':True,'sources':{s:current['sources'][r]['path'] for s,(r,_,_) in apps.SERVICES.items()},
        'dependencies':{s:current['deps'][s]['modules'] for s in apps.SERVICES},'old_volumes_retained':True,
        'identities_unchanged':True,'workspaces_unchanged':True,'health':health})
    print('PASS: running backends use HDD source/dependencies; workspaces, keys and old volumes retained.')


if __name__=='__main__':main()
