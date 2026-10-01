"""Offline protocol and persistence checks for single-member chart Mongo profiles."""
import hashlib
import json
import socket
import subprocess
import time


def external(m, js, uri=None):
    uri = uri or (m.OUT / 'compass-uri.txt').read_text().strip()
    creds = json.loads((m.IDENTITY / 'credentials.json').read_text())
    source = 'assert.eq=assert.equal;const uri=' + json.dumps(uri + '&serverSelectionTimeoutMS=10000&connectTimeoutMS=3000') + ';const conn=new Mongo(uri);const d=conn.getDB("chart");\n' + js
    p = subprocess.run(['docker','run','--rm','-i','--network','host','--user','1000:1000','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev','--env','HOME=/tmp',
        '--entrypoint','mongosh',m.IMAGE,'--nodb','--quiet','--file','/dev/stdin'],
        input=source,text=True,capture_output=True,timeout=120)
    if p.returncode:
        error=p.stderr+p.stdout
        for key in ('password','admin_password'): error=error.replace(creds[key],'[redacted]')
        raise RuntimeError(error[-2000:])
    return json.loads(p.stdout)


def check(m):
    inv=m.inventory()
    m.ready()
    result=external(m,r'''
const h=d.hello();assert(h.isWritablePrimary);assert.eq(h.setName,'rs0');assert.eq(h.hosts.length,1);
d.persistence.updateOne({_id:'retained'},{$setOnInsert:{token:'single-member-fixture-v1',createdAt:new Date()}},{upsert:true,writeConcern:{w:'majority',wtimeout:10000}});
const retained=d.persistence.findOne({_id:'retained'});assert.eq(retained.token,'single-member-fixture-v1');
let denied=false;try{conn.getDB('admin').runCommand({usersInfo:1});}catch(e){denied=e.code===13;}assert(denied);
const session=conn.startSession();const sd=session.getDatabase('chart');
session.startTransaction({readConcern:{level:'snapshot'},writeConcern:{w:'majority'}});
sd.transactions.updateOne({_id:'committed'},{$set:{ok:true}},{upsert:true});session.commitTransaction();session.endSession();
const stream=d.changes.watch([], {maxAwaitTimeMS:500});stream.tryNext();const id=new ObjectId();d.changes.insertOne({_id:id,synthetic:true});
let event=null;for(let i=0;i<10&&!event;i++){event=stream.tryNext();}stream.close();assert(event && event.documentKey._id.equals(id));
let unauth=false;const plain=new Mongo(uri.replace(/\/\/[^@]+@/,'//'));
try{plain.getDB('chart').persistence.findOne({});}catch(e){unauth=e.code===13;}assert(unauth);
print(JSON.stringify({directIpConnection:true,replicaSet:h.setName,primary:h.isWritablePrimary,advertisedMembers:h.hosts,retained,majorityTransaction:true,changeStream:true,unauthenticatedDenied:unauth,adminDenied:denied}));
''')
    assert result['advertisedMembers']==[m.INT[0]+':27017']
    internal=(m.OUT/'internal-uri.txt').read_text().strip()
    hosts=json.loads(m.shell('const c=new Mongo('+json.dumps(internal)+');const d=c.getDB("chart");assert.eq(d.persistence.findOne({_id:"retained"}).token,"single-member-fixture-v1");print(JSON.stringify(d.hello().hosts));',auth=False))
    assert hosts==[m.INT[0]+':27017']
    config=json.loads(m.shell('print(JSON.stringify({conf:rs.conf(),opts:db.adminCommand({getCmdLineOpts:1}).parsed}));'))
    assert len(config['conf']['members'])==1 and not config['conf']['members'][0].get('horizons')
    assert config['opts']['net'].get('tls',{}).get('mode','disabled')=='disabled'
    assert config['opts']['security']['keyFile']=='/run/mongo/keyfile'
    node=json.loads(m.run(['kubectl','get','node',m.load('host.json')['node'],'-o','json'],capture=True))
    lan=next(a['address'] for a in node['status']['addresses'] if a['type']=='InternalIP')
    host=m.load('host.json')['tailscale']
    try:
        with socket.create_connection((lan,inv['port']),timeout=3): raise AssertionError('LAN listener found')
    except (ConnectionError,TimeoutError): pass
    # A proxy TCP handshake alone can succeed before its source ACL closes it.
    with socket.create_connection((host,inv['port']),source_address=(lan,0),timeout=3) as sock:
        try:
            assert sock.recv(1)==b'', 'LAN-sourced connection was not closed'
        except ConnectionResetError: pass
    pv=m.kget('pv',m.PV,None)
    assert pv['spec']['local']['path']==str(m.DATA) and pv['spec']['persistentVolumeReclaimPolicy']=='Retain'
    pods=json.loads(m.run(['kubectl','-n',m.NS,'get','pods','-o','json'],capture=True))['items']
    assert len(pods)==1
    for c in pods[0]['spec']['containers']:
        assert not c.get('resources',{}).get('requests') and not c.get('resources',{}).get('limits')
    services=json.loads(m.run(['kubectl','-n',m.NS,'get','services','-o','json'],capture=True))['items']
    assert {s['metadata']['name'] for s in services}=={'mongo','mongo-access'}
    assert all(s['spec']['type']=='ClusterIP' for s in services)
    before=m.load('dns-pilot/before.json')
    current={}
    for o in json.loads(m.run(['kubectl','get','deployments,statefulsets,daemonsets,services','-A','-o','json'],capture=True))['items']:
        md=o['metadata']; ns=md.get('namespace')
        if ns.startswith('chart-'): continue
        current[f"{ns}/{o['kind']}/{md['name']}"]={'uid':md['uid'],'spec_sha256':hashlib.sha256(json.dumps(o['spec'],sort_keys=True).encode()).hexdigest()}
    cm=m.kget('configmap','coredns','kube-system')
    current['kube-system/ConfigMap/coredns']={'uid':cm['metadata']['uid'],'data_sha256':hashlib.sha256(json.dumps(cm['data'],sort_keys=True).encode()).hexdigest()}
    assert current==before, 'Unrelated workloads changed'
    report={'time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'profile':m.PROFILE,'passed':True,'protocol':result,
        'internal_discovery':hosts,'one_member_no_horizons_no_tls':True,'keyfile_auth':True,
        'host_lan_destination_and_source_denied':True,'one_retained_hdd_volume':True,
        'no_cpu_memory_requests_or_limits':True,'no_nodeport_or_member_services':True,
        'unrelated_workloads_unchanged':True,'actual_mac_compass_tested':False}
    m.save(f'profiles/{m.PROFILE}/verification.json',report)
    print(json.dumps(report,indent=2))
