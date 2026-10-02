#!/usr/bin/env python3
"""Retained synthetic login/workspace and runtime-boundary checks inside a box."""
import argparse
import fcntl
import json
import re
import socket
import time
import urllib.error
import urllib.request

import backing as b
import box

EMAIL = 'box-browser@chart.test'


def request(ip, route, method='GET', body=None, token=None):
    headers = {'Content-Type': 'application/json', 'Origin': 'http://localhost:8097'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    r = urllib.request.Request('http://' + ip + ':3000/api/v1/' + route,
                               data=json.dumps(body).encode() if body is not None else None,
                               method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        data = json.loads(error.read())
        raise RuntimeError(f'{method} {route}: HTTP {error.code}, tag={data.get("tag")}, message={data.get("message")}')


def seed(existing):
    code = '''
const d=db.getSiblingDB('auth');
for(const [id,name] of [['c1a000000000000000000001','guest'],['c1a000000000000000000002','frontend_dev']]){
 const found=d.roles.findOne({name});
 if(found){assert.equal(found._id.toString(),id,'Refusing another role with fixture name');}
 else{assert(!EXISTING,'Expected retained role');assert(!d.roles.findOne({_id:ObjectId(id)}));
  d.roles.insertOne({_id:ObjectId(id),name,permissions:{},types:[],BARS_LIMIT:1000,chartInfraFixture:'incus-app-v1'});}
}
const id=ObjectId('c1a000000000000000000003');const email='box-browser@chart.test';
const found=d.users.findOne({email});
if(found){assert.equal(found._id.toString(),id.toString());assert.equal(found.chartInfraFixture,'incus-app-v1');}
else{assert(!EXISTING,'Expected retained user');assert(!d.users.findOne({_id:id}));
 d.users.insertOne({_id:id,email,username:'box-browser',firstName:'Box',lastName:'Fixture',
 role:ObjectId('c1a000000000000000000002'),isActive:true,isVerified:true,accountType:'user',
 twoFA:null,createdAt:new Date(),updatedAt:new Date(),chartInfraFixture:'incus-app-v1'});}
print('Synthetic roles/user verified.');
'''.replace('EXISTING', 'true' if existing else 'false')
    b.mongo_js(code)


def verify(inv, existing):
    selected = box.selection()
    objects = box.containers()
    b.require(len(objects) == 3, 'Expected all three application containers')
    for obj in objects:
        host = obj['HostConfig']
        b.require(obj['State'].get('Health', {}).get('Status') == 'healthy', 'App unhealthy')
        b.require(obj['Config']['User'] == '1000:1000' and host['ReadonlyRootfs'], 'App privilege differs')
        b.require(set(obj['NetworkSettings']['Networks']) == {'chart-backing-runtime'}, 'Unexpected app network')
        b.require(not host['PortBindings'] and host['RestartPolicy']['Name'] == 'no', 'Unexpected publication/autostart')
        for f in ('Memory', 'MemoryReservation', 'NanoCpus', 'CpuQuota', 'CpuShares'):
            b.require(host[f] == 0, 'Unexpected resource reservation/cap: ' + f)
        for mount in obj['Mounts']:
            if mount['Type'] in ('bind', 'volume'):
                b.require(not mount['RW'] and 'npmrc' not in mount['Source'], 'Writable/private installer runtime mount')
    network = json.loads(b.run(['docker', 'network', 'inspect', 'chart-backing-runtime']))[0]
    b.require(network['Internal'] and not network['EnableIPv6'], 'Runtime network differs')
    targets = [inv['tailscale_ip'], network['IPAM']['Config'][0]['Gateway']]
    egress = {}
    for obj in objects:
        service = obj['Config']['Labels']['com.docker.compose.service']
        egress[service] = []
        for target in targets:
            with socket.socket() as listener:
                listener.bind((target, 0)); listener.listen()
                port = listener.getsockname()[1]
                with socket.create_connection((target, port), timeout=2):
                    accepted, _ = listener.accept(); accepted.close()
                js = "const n=require('node:net');const start=Date.now();const s=n.connect(" + json.dumps({'host': target, 'port': port}) + ");s.setTimeout(2000);s.on('connect',()=>{console.log('CONNECTED');s.destroy()});s.on('timeout',()=>{console.log('TIMEOUT');s.destroy()});s.on('error',e=>console.log(JSON.stringify({code:e.code,ms:Date.now()-start})));"
                result = b.run(['docker', 'exec', obj['Id'], 'node', '-e', js])
                r = json.loads(result)
                b.require(r['code'] in ('ENETUNREACH', 'ECONNREFUSED', 'EHOSTUNREACH') and r['ms'] < 1500,
                          'Expected prompt controlled egress denial')
                egress[service].append({'target': target, **r})
    seed(existing)
    ip = inv['tailscale_ip']
    health = request(ip, 'health/services')
    b.require(health['status'] == 'ok', 'Gateway dependency health failed')
    request(ip, 'auth-v2/login-code/request', 'POST', {'email': EMAIL})
    auth = next(c for c in objects if c['Config']['Labels']['com.docker.compose.service'] == 'auth')
    logs = b.run(['docker', 'logs', '--since', '2m', auth['Id']])
    codes = re.findall(r'sign-in code for ' + re.escape(EMAIL) + r': (\d{6})', logs)
    b.require(codes, 'Offline code not logged')
    login = request(ip, 'auth-v2/login-code/verify', 'POST', {'email': EMAIL, 'code': codes[-1]})
    payload = login.get('data', login)
    token = payload.get('accessToken')
    b.require(token, 'Login did not produce a session')
    state = box.STATE / 'api-fixture.json'
    if not state.exists():
        b.require(not existing, 'Retained workspace receipt missing')
        created = request(ip, 'workspaces', 'POST', {'name': 'Incus retained fixture', 'isMain': False,
                          'offchart': [], 'onchart': [], 'metadata': {}, 'multiCharts': {'mode': '1', 'workspaces': []}}, token)
        workspace = created.get('data', created)
        ident = workspace.get('id') or workspace.get('_id')
        b.require(ident, 'Workspace creation response missing ID')
        b.save(state, {'workspace': ident, 'email': EMAIL, 'name': 'Incus retained fixture'})
    fixture = b.read(state)
    got = request(ip, 'workspaces/' + fixture['workspace'], token=token)
    row = got.get('data', got)
    b.require(row.get('name') == fixture['name'], 'Retained workspace differs')
    result = {'box': inv['box'], 'sourceWorkspace': selected['workspace'], 'verifiedAt': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'health': health['services'], 'offlineLogin': True, 'workspaceId': fixture['workspace'],
              'retainedWorkspace': True, 'expectExisting': existing, 'controlledEgressDenial': egress,
              'runtimeUid': 1000, 'readOnlyRuntimeMounts': True, 'noCpuMemoryReservationsOrLimits': True,
              'actualLaptopBrowserTested': False}
    reports = box.STATE / 'verification'; reports.mkdir(mode=0o700, exist_ok=True)
    b.save(reports / (str(time.time_ns()) + '.json'), result)
    print(json.dumps(result, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--box', required=True)
    p.add_argument('--expect-existing', action='store_true')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    marker = b.guard(a.box)
    with open('/run/lock/chart-backing.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        b.ownership(a.box)
        inv = b.inventory(a.box, marker)
        if not a.apply:
            print('Plan: retained synthetic roles/user/workspace, offline login, API and runtime boundary checks.')
            return
        verify(inv, a.expect_existing)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error))
