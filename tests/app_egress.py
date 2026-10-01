#!/usr/bin/env python3
"""Controlled cross-namespace TCP denial from the real app Pod; no live APIs."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apps
import mongo
from common import apply, guard, lock, run, save
from sources import valid_name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=valid_name, default='sean')
    args = parser.parse_args()
    guard()
    held = lock('chart-apps-'+args.profile)
    apps.setup(args.profile)
    inv = apps.record()
    ns = 'chart-egress-check-'+str(time.time_ns())
    labels = {'app.kubernetes.io/managed-by':'chart-egress-check'}
    namespace = {'apiVersion':'v1','kind':'Namespace','metadata':{'name':ns,'labels':{
        **labels,'pod-security.kubernetes.io/enforce':'restricted'}}}
    server = "require('node:http').createServer((q,s)=>s.end('chart-egress-fixture')).listen(8080,'0.0.0.0')"
    pod = {'apiVersion':'v1','kind':'Pod','metadata':{'name':'sink','namespace':ns,'labels':labels},
           'spec':{'activeDeadlineSeconds':180,'restartPolicy':'Never','automountServiceAccountToken':False,
                   'securityContext':apps.podsecurity(),'containers':[{
                       'name':'sink','image':inv['image'],'command':['node','-e',server],
                       'securityContext':apps.containersecurity(),
                       'readinessProbe':{'tcpSocket':{'port':8080},'periodSeconds':1}}]}}
    try:
        # No target ingress policy: denial must come from the app's egress policy.
        apply([namespace,pod])
        run(['kubectl','-n',ns,'wait','pod/sink','--for=condition=Ready','--timeout=60s'])
        ip = run(['kubectl','-n',ns,'get','pod','sink','-o','jsonpath={.status.podIP}'],capture=True)
        def control():
            with urllib.request.urlopen('http://'+ip+':8080',timeout=3) as response:
                assert response.read() == b'chart-egress-fixture'
        control()
        probe = '''const net=require('node:net');
function connect(host,port){return new Promise(resolve=>{const s=net.connect({host,port});
const finish=v=>{s.destroy();resolve(v)};s.setTimeout(3000,()=>finish('timeout'));
s.once('connect',()=>finish('connected'));s.once('error',e=>finish(e.code));});}
(async()=>console.log(JSON.stringify({allowed:await connect('mongo',27017),
denied:await connect(process.argv[1],8080)})))().catch(()=>process.exit(1));'''
        result = json.loads(run(['kubectl','-n',mongo.NS,'exec','deployment/tharamine','--',
                                 'node','-e',probe,ip],capture=True))
        control()
        assert result['allowed'] == 'connected', result
        # k3s may actively REJECT denied traffic instead of silently dropping it.
        # The host controls on both sides prove the listener was available.
        assert result['denied'] in ('timeout','ECONNREFUSED','EHOSTUNREACH','ENETUNREACH','EACCES','EPERM'), result
        policies = json.loads(run(['kubectl','-n',mongo.NS,'get','networkpolicy','-o','json'],capture=True))
        evidence = {'passed':True,'profile':args.profile,'workspace':inv['workspace'],
                    'fixture_reachable_from_host_before_and_after':True,**result,
                    'policies':[{'name':o['metadata']['name'],'spec':o['spec']} for o in policies['items']],
                    'scope':'Real app Pod to controlled cross-namespace endpoint. No public API contacted; not a proof against privileged host access or future policy changes.'}
        save('profiles/'+args.profile+'/stock-egress.json',evidence)
        print('PASS: real Tharamine Pod reaches profile Mongo; controlled cross-namespace TCP is denied.')
    finally:
        # This unique namespace contains only the ephemeral deadline-bound sink.
        run(['kubectl','delete','namespace',ns,'--ignore-not-found','--wait=false'])


if __name__ == '__main__':
    main()
