#!/usr/bin/env python3
"""Verify Sean's dedicated sync runtime; record untouched OpenScape and identity retention."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import apps,mongo,sync
from common import guard,load,lock,run,save


def existing():
    obj=json.loads(run(['kubectl','-n','openscape-sync','get','deployment','syncthing','-o','json'],capture=True))
    cfg=Path('/home/sean/services/openscape-sync/state/config.xml')
    return {'deployment_spec':obj['spec'],'config_sha256':hashlib.sha256(cfg.read_bytes()).hexdigest()}


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['before','check','retention']);a=p.parse_args()
    guard();held=lock('chart-sync-sean');apps.setup('sean')
    if a.action=='before':
        save('profiles/sean/sync-openscape-before.json',existing());print('Recorded existing OpenScape deployment/config fingerprint.');return
    inv=sync.inventory();before_identity=inv['identity'];before_volumes=inv['volume_uids']
    if a.action=='retention':
        sync.down(True);sync.up();inv=sync.inventory()
        assert inv['identity']==before_identity and inv['volume_uids']==before_volumes
    with sync.client() as api:
        cfg=api.call('config');status=api.call('system/status');version=api.call('system/version')['version']
        assert status['myID']==inv['device_id']
        for key in ('globalAnnounceEnabled','localAnnounceEnabled','relaysEnabled','natEnabled','crashReportingEnabled'):
            assert cfg['options'][key] is False,key
        assert cfg['options']['listenAddresses']==['tcp://0.0.0.0:22000']
        assert cfg['options']['urAccepted']==-1
        assert cfg['gui']['user']=='chart-sean' and cfg['gui']['password']
        assert len(cfg['folders'])==3
        assert all(f['type']=='receiveonly' and f['paused'] for f in cfg['folders'])
        for f in cfg['folders']:assert api.call('db/ignores',folder=f['id'])['ignore']==sync.IGNORES
    pods=json.loads(run(['kubectl','-n','chart-sean','get','pods','-l','chart-app=syncthing','-o','json'],capture=True))['items']
    assert len(pods)==1
    pod=pods[0]
    for c in pod['spec']['containers']:assert not c.get('resources',{}).get('requests') and not c.get('resources',{}).get('limits')
    assert not pod['spec']['automountServiceAccountToken']
    mongo_ip=mongo.kget('service','mongo-access')['spec']['clusterIP']
    probes={}
    for label,host,port in [('mongo',mongo_ip,27017),('external-documentation','203.0.113.1',443)]:
        r=subprocess.run(['kubectl','-n','chart-sean','exec',pod['metadata']['name'],'--','nc','-z','-w','2',host,str(port)],capture_output=True,timeout=10)
        assert r.returncode!=0,label+' unexpectedly reachable'
        assert b'not found' not in r.stderr, 'Network test binary unavailable'
        probes[label]='denied'
    with socket.create_connection(('100.66.127.115',inv['port']),timeout=5):pass
    assert existing()==load('profiles/sean/sync-openscape-before.json'),'Existing OpenScape config/spec changed'
    health=json.load(urllib.request.urlopen('http://100.66.127.115:13000/api/v1/health/services',timeout=15))
    assert all(v['db']=='up' for v in health['services'].values())
    save('profiles/sean/sync-'+a.action+'.json',{'passed':True,'device_id':inv['device_id'],'version':version,
        'paused_unpaired_folders':3,'network':probes,'no_resource_limits':True,'openscape_unchanged':True,
        'identity_retained':inv['identity']==before_identity,'volumes_retained':inv['volume_uids']==before_volumes})
    print('PASS: dedicated sync identity, exclusions, network isolation, existing OpenScape and API health.')


if __name__=='__main__':main()
