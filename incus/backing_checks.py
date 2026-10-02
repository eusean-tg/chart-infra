#!/usr/bin/env python3
"""Synthetic backing-service acceptance; fixture records are retained."""
import argparse
import fcntl
import json
import socket
import subprocess
import time
from pathlib import Path
import backing as b


def verify(inv, existing):
    users = b.read(b.STATE / 'credentials.json')['mongo']
    fixture = users['fixture']
    uri = 'mongodb://' + fixture['user'] + ':' + fixture['password'] + '@mongo:27017/chart_fixture?authSource=admin&replicaSet=rs0&serverSelectionTimeoutMS=5000'
    code = 'const uri=' + json.dumps(uri) + '; const c=new Mongo(uri); const d=c.getDB("chart_fixture");\n'
    if not existing:
        code += 'd.retained.updateOne({_id:"backing-v1"},{$setOnInsert:{token:"incus-backing-fixture-v1"}},{upsert:true,writeConcern:{w:"majority"}});\n'
    code += '''
assert.equal(d.retained.findOne({_id:"backing-v1"}).token,"incus-backing-fixture-v1");
assert.equal(c.getDB("admin").hello().setName,"rs0");
d.transactions.updateOne({_id:"collection-init"},{$setOnInsert:{synthetic:true}},{upsert:true});
const session=c.startSession();const sd=session.getDatabase("chart_fixture");
session.startTransaction({readConcern:{level:"snapshot"},writeConcern:{w:"majority"}});
sd.transactions.updateOne({_id:"committed"},{$set:{token:"transaction-v1"}},{upsert:true});
session.commitTransaction();session.endSession();
assert.equal(d.transactions.findOne({_id:"committed"}).token,"transaction-v1");
const stream=d.changes.watch([],{maxAwaitTimeMS:500});stream.tryNext();
const id=new ObjectId();d.changes.insertOne({_id:id,synthetic:true});
let event=null;for(let i=0;i<10&&!event;i++){event=stream.tryNext();}stream.close();
assert(event&&event.documentKey._id.equals(id));
const plain=new Mongo("mongodb://mongo:27017/?directConnection=true&serverSelectionTimeoutMS=5000");
let denied=false;try{plain.getDB("chart_fixture").retained.findOne({});}catch(e){denied=e.code===13;}assert(denied);
denied=false;try{c.getDB("admin").getUsers();}catch(e){denied=e.code===13;}assert(denied);
'''
    for name, user in users.items():
        if name == 'admin':
            continue
        u = 'mongodb://' + user['user'] + ':' + user['password'] + '@mongo:27017/?authSource=admin&replicaSet=rs0&serverSelectionTimeoutMS=5000'
        code += '{const u=new Mongo(' + json.dumps(u) + ');const d=u.getDB(' + json.dumps(user['db']) + ');'
        code += 'd.chart_infra_verification.updateOne({_id:"user-scope-v1"},{$setOnInsert:{synthetic:true}},{upsert:true});'
        other = 'auth' if user['db'] != 'auth' else 'orange'
        code += 'let denied=false;try{u.getDB(' + json.dumps(other) + ').chart_infra_verification.findOne({});}catch(e){denied=e.code===13;}assert(denied);}\n'
    code += '''
assert.equal(rs.conf().members.length,1);assert.equal(rs.conf().members[0].host,"mongo:27017");
const opts=db.adminCommand({getCmdLineOpts:1}).parsed;
assert.equal(opts.security.authorization,"enabled");assert.equal(opts.security.keyFile,"/run/chart/keyfile");
assert.equal(opts.storage.wiredTiger.engineConfig.cacheSizeGB,0.5);
print(JSON.stringify({mongoVersion:db.version(),primary:db.hello().isWritablePrimary,
 internalReplicaDiscovery:true,retainedToken:true,transaction:true,changeStream:true,
 unauthenticatedDenied:true,perServiceDatabaseIsolation:true,keyfile:true,wiredTigerCacheGiB:0.5}));
'''
    result = json.loads(b.mongo_js(code))
    if not existing:
        b.redis_command('SET', 'chart-infra:backing-v1', 'incus-cache-fixture-v1', 'NX')
    b.require(b.redis_command('GET', 'chart-infra:backing-v1') == 'incus-cache-fixture-v1', 'Cache fixture missing/different')
    try:
        b.redis_command('GET', 'chart-infra:backing-v1', authenticated=False)
    except RuntimeError:
        pass
    else:
        raise RuntimeError('Unauthenticated cache read succeeded')
    info = b.redis_command('INFO', 'server')
    version = dict(line.split(':', 1) for line in info.splitlines() if ':' in line)
    result['dragonflyVersion'] = version.get('dragonfly_version')
    b.require(result['dragonflyVersion'], 'Dragonfly version unavailable')
    result.update(cacheAuthentication=True, cacheRetainedToken=True)
    net = json.loads(b.run(['docker', 'network', 'inspect', b.PROJECT + '-runtime']))[0]
    b.require(net['Internal'] and not net['EnableIPv6'], 'Expected internal IPv4 network')
    for service in ('mongo', 'redis'):
        obj = b.container(service)
        h = obj['HostConfig']
        for field in ('Memory', 'MemoryReservation', 'NanoCpus', 'CpuQuota', 'CpuShares'):
            b.require(h[field] == 0, 'Unexpected resource cap/reservation: ' + field)
        b.require(h['RestartPolicy']['Name'] == 'no', 'Unexpected auto restart')
        b.require(obj['Config']['User'] == '999:999' and h['ReadonlyRootfs'], 'Runtime privilege differs')
        b.require(set(obj['NetworkSettings']['Networks']) == {b.PROJECT + '-runtime'}, 'Unexpected runtime network')
        b.require(not h['PortBindings'], 'Unexpected Docker port publication')
        b.require(obj['State'].get('Health', {}).get('Status') == 'healthy', service + ' health check not passing')
        if service == 'mongo':
            limits = {v['Name']: v for v in (h.get('Ulimits') or [])}
            b.require(limits.get('nofile') == {'Name': 'nofile', 'Soft': 64000, 'Hard': 64000},
                      'Mongo open-file capacity differs')
            b.require(b.run(['docker', 'exec', obj['Id'], 'sh', '-c', 'ulimit -n']) == '64000',
                      'Effective Mongo open-file ceiling differs')
            result['mongoOpenFiles'] = 64000
    # A controlled listener on the guest's own Tailscale IP proves runtime denial without vendor traffic.
    with socket.socket() as listener:
        listener.bind((inv['tailscale_ip'], 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection((inv['tailscale_ip'], port), timeout=2):
            accepted, _ = listener.accept()
            accepted.close()
        begin = time.monotonic()
        probe = subprocess.run([*b.DOCKER, 'exec', b.container('mongo')['Id'], 'timeout', '2', 'bash', '-c',
                                f': >/dev/tcp/{inv["tailscale_ip"]}/{port}'], capture_output=True, text=True, timeout=5)
        elapsed = round((time.monotonic() - begin) * 1000)
        b.require(probe.returncode != 0 and probe.returncode != 124
                  and 'Network is unreachable' in probe.stderr, 'Expected immediate runtime network-unreachable denial')
        result['controlledEgressDenial'] = {'connected': False, 'code': 'ENETUNREACH', 'ms': elapsed}
    result.update(noCpuMemoryLimitsOrReservations=True, internalOnlyDockerNetwork=True,
                  box=inv['box'], dataset=inv['dataset'], expectExisting=existing,
                  verifiedAt=time.strftime('%Y-%m-%dT%H:%M:%S%z'), actualMacCompassTested=False)
    reports = b.STATE / 'verification'
    b.safe(reports).mkdir(mode=0o700, exist_ok=True)
    path = reports / (str(time.time_ns()) + '.json')
    b.save(path, result)
    print(json.dumps(result, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--box', required=True)
    p.add_argument('--expect-existing', action='store_true', help='Fail if retained Mongo/cache tokens are missing; never recreate them')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    marker = b.guard(a.box)
    with open('/run/lock/chart-backing.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        b.ownership(a.box)
        inv = b.inventory(a.box, marker)
        b.require(inv['phase'] == 'ready', 'Backing initialization incomplete')
        if not a.apply:
            print('Plan: retained synthetic DB/cache records, transactions, change streams, user isolation and controlled egress checks.')
            return
        verify(inv, a.expect_existing)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error))
