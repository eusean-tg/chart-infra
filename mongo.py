#!/usr/bin/env python3
"""Reusable single-member Mongo replica sets with retained HDD storage. No source fetching, production or live APIs."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

from common import STATE, apply, guard, load, lock, run, save

IMAGE = 'docker.io/library/mongo@sha256:609d76574151bee8b148caee83cd9fec8b5f695e5a6db9f1e0f9dced0673574e'
HDD_UUID = '14ef1cfa-28ee-4986-8989-2e248896bf07'
CLAIM = 'mongo-data'
DEFAULT_PORTS = {'mongo-pilot-single':27017, 'sean':27018, 'alex':27019, 'ryan':27020, 'gerald':27021}


def configure(profile='mongo-pilot-single'):
    import re
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', profile) or profile == 'mongo-pilot':
        raise SystemExit('Invalid/reserved profile name')
    global PROFILE, NS, OWNER, BASE, DATA, IDENTITY, OUT, PV, INT
    PROFILE = profile
    NS = 'chart-' + profile
    OWNER = {'app.kubernetes.io/managed-by': NS}
    BASE = Path('/mnt/hdd/shared-dev/profiles') / profile
    DATA, IDENTITY = BASE / 'mongo', BASE / 'identity'
    OUT = STATE / 'profiles' / profile
    PV = NS
    INT = [f'mongo-0.mongo.{NS}.svc.cluster.local']


configure()


def kget(kind, name, ns='profile'):
    if ns == 'profile': ns = NS
    args = ['kubectl', 'get', kind, name, '--ignore-not-found', '-o', 'json']
    if ns:
        args += ['-n', ns]
    raw = run(args, capture=True)
    return json.loads(raw) if raw.strip() else None


def owned(obj):
    if obj and obj['metadata'].get('labels', {}).get('app.kubernetes.io/managed-by') != NS:
        raise SystemExit('Refusing an object not owned by this pilot')


def meta(name, namespace=True):
    return {'name': name, 'labels': OWNER.copy(), **({'namespace': NS} if namespace else {})}


def object_(kind, name, spec=None, api='v1', namespace=True, **extra):
    return {'apiVersion': api, 'kind': kind, 'metadata': meta(name, namespace),
            **({'spec': spec} if spec is not None else {}), **extra}


def write_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def hdd_guard():
    mount = run(['findmnt', '-n', '-o', 'TARGET,UUID', '--target', '/mnt/hdd'], capture=True).split()
    if mount != ['/mnt/hdd', HDD_UUID]:
        raise SystemExit('Expected HDD mount/UUID missing; refusing storage operation')
    for path in (BASE, DATA, IDENTITY):
        if path.resolve() != path:
            raise SystemExit('Refusing symlinked pilot storage')


def inventory():
    hdd_guard()
    inv = json.loads((IDENTITY / 'inventory.json').read_text())
    if inv['image'] != IMAGE or inv['hdd_uuid'] != HDD_UUID:
        raise SystemExit('Stored Mongo image/disk identity differs; manual migration required')
    for i in range(1):
        p = DATA / 'default' / f'member-{i}'
        if p.resolve() != p or not p.is_dir() or (p / '.shared-dev-member').read_text() != inv['markers'][i]:
            raise SystemExit('Missing/mismatched member directory; refusing empty replacement')
    if inv.get('initialized') and not (DATA / 'default/member-0/WiredTiger').is_file():
        raise SystemExit('Initialized dataset lost its database files; refusing replacement')
    if (DATA / '.shared-dev-volume').read_text() != inv['volume_marker']:
        raise SystemExit('Volume identity mismatch')
    if 'pv_uid' in inv:
        for kind, name, ns, key in [('pv', PV, None, 'pv_uid'), ('pvc', CLAIM, NS, 'pvc_uid')]:
            obj = kget(kind, name, ns)
            owned(obj)
            if not obj or obj['metadata']['uid'] != inv[key]:
                raise SystemExit('Retained PV/PVC missing or replaced')
    return inv


def prepare(port=None):
    hdd_guard()
    OUT.mkdir(parents=True, exist_ok=True)
    if (IDENTITY / 'inventory.json').exists():
        inv = inventory()
        if port and port != inv['port']: raise SystemExit('Existing profile port differs; refusing implicit route change')
        print('Existing identity and data retained.')
        return
    if BASE.exists():
        raise SystemExit('Pilot path exists without inventory; inspect rather than overwrite')
    port = port or DEFAULT_PORTS.get(PROFILE)
    if not port or not 1024 <= port <= 65535:
        raise SystemExit('Supply --port between 1024 and 65535')
    for other in (STATE / 'profiles').glob('*/profile.json'):
        if json.loads(other.read_text())['port'] == port:
            raise SystemExit('Port already reserved by another chart profile')
    BASE.mkdir(parents=True, mode=0o700)
    IDENTITY.mkdir(mode=0o700)
    DATA.mkdir(mode=0o700)
    inv = {'image': IMAGE, 'hdd_uuid': HDD_UUID, 'profile': PROFILE, 'port': port, 'database': 'chart', 'volume_marker': secrets.token_hex(24),
           'markers': [secrets.token_hex(24) for _ in range(1)], 'initialized': False}
    (DATA / '.shared-dev-volume').write_text(inv['volume_marker'])
    for i in range(1):
        p = DATA / 'default' / f'member-{i}'
        p.mkdir(parents=True, mode=0o700)
        (p / '.shared-dev-member').write_text(inv['markers'][i])
    creds = {'admin_user': 'pilot_admin', 'admin_password': secrets.token_hex(24),
             'user': 'profile_' + PROFILE.replace('-', '_'), 'password': secrets.token_hex(24)}
    write_json(IDENTITY / 'credentials.json', creds)
    (IDENTITY / 'keyfile').write_text(base64.b64encode(secrets.token_bytes(384)).decode())
    write_json(IDENTITY / 'inventory.json', inv)
    write_json(OUT / 'profile.json', {'profile': PROFILE, 'namespace': NS, 'port': port})
    connections(inv)
    print('Prepared new empty single-member dataset and per-profile identity on HDD.')


def connections(inv):
    c = json.loads((IDENTITY / 'credentials.json').read_text())
    auth = c['user'] + ':' + c['password'] + '@'
    host = load('host.json')['tailscale']
    (OUT / 'compass-uri.txt').write_text('mongodb://' + auth + host + ':' + str(inv['port']) + '/chart?authSource=admin&directConnection=true\n')
    (OUT / 'internal-uri.txt').write_text('mongodb://' + auth + INT[0] + ':27017/chart?authSource=admin&replicaSet=rs0\n')


def manifests(inv):
    host = load('host.json')['tailscale']
    node = load('host.json')['node']
    ns = object_('Namespace', NS, namespace=False)
    ns['metadata']['labels']['pod-security.kubernetes.io/enforce'] = 'restricted'
    ns['metadata']['annotations'] = {'shared-dev/purpose': 'Single-member Mongo replica set; non-root, no host networking, hostPath or API token'}
    sc = object_('StorageClass', PV, api='storage.k8s.io/v1', namespace=False,
                 provisioner='kubernetes.io/no-provisioner', reclaimPolicy='Retain', volumeBindingMode='WaitForFirstConsumer')
    pv = object_('PersistentVolume', PV, {'capacity': {'storage': '50Gi'}, 'volumeMode': 'Filesystem',
        'accessModes': ['ReadWriteOnce'], 'persistentVolumeReclaimPolicy': 'Retain', 'storageClassName': PV,
        'local': {'path': str(DATA)}, 'claimRef': {'namespace': NS, 'name': CLAIM},
        'nodeAffinity': {'required': {'nodeSelectorTerms': [{'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [node]}]}]}}}, namespace=False)
    pvc = object_('PersistentVolumeClaim', CLAIM, {'accessModes': ['ReadWriteOnce'], 'storageClassName': PV,
        'volumeName': PV, 'resources': {'requests': {'storage': '50Gi'}}})
    sec = object_('Secret', 'mongo-membership', type='Opaque', data={'keyfile': base64.b64encode((IDENTITY / 'keyfile').read_bytes()).decode()})
    start = '''set -eu
case "$POD_INDEX" in 0) ;; *) exit 41;; esac
[ "$(cat /volume/.shared-dev-volume)" = "$VOLUME_MARKER" ]
expected=$(cat /config/marker-$POD_INDEX)
[ "$(cat /data/db/.shared-dev-member)" = "$expected" ]
[ "$(cat /volume/default/member-$POD_INDEX/.shared-dev-member)" = "$expected" ]
[ ! -f /data/db/.shared-dev-initialized ] || [ -f /data/db/WiredTiger ]
umask 077
cp /secrets/keyfile /run/mongo/keyfile
exec mongod --bind_ip_all --port 27017 --replSet rs0 --dbpath /data/db --keyFile /run/mongo/keyfile --wiredTigerCacheSizeGB 0.5 --oplogSize 256
'''
    cm = object_('ConfigMap', 'mongo-config', data={'start.sh': start, **{f'marker-{i}': inv['markers'][i] for i in range(1)}})
    security = {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}}
    csecurity = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
    mounts = [{'name': 'data', 'mountPath': '/data/db', 'subPathExpr': 'default/member-$(POD_INDEX)'},
              {'name': 'data', 'mountPath': '/volume', 'readOnly': True},
              {'name': 'secret', 'mountPath': '/secrets', 'readOnly': True},
              {'name': 'config', 'mountPath': '/config', 'readOnly': True},
              {'name': 'runtime', 'mountPath': '/run/mongo'}, {'name': 'tmp', 'mountPath': '/tmp'}]
    pod = {'nodeSelector': {'kubernetes.io/hostname': node}, 'automountServiceAccountToken': False,
           'securityContext': security, 'terminationGracePeriodSeconds': 90,
           'containers': [{'name': 'mongo', 'image': IMAGE, 'command': ['/bin/sh', '/config/start.sh'],
              'securityContext': csecurity, 'env': [{'name': 'POD_INDEX', 'valueFrom': {'fieldRef': {'fieldPath': "metadata.labels['apps.kubernetes.io/pod-index']"}}},
                 {'name': 'VOLUME_MARKER', 'value': inv['volume_marker']}, {'name': 'HOME', 'value': '/tmp'}],
              'ports': [{'name': 'mongo', 'containerPort': 27017}], 'volumeMounts': mounts,
              'startupProbe': {'tcpSocket': {'port': 27017}, 'periodSeconds': 3, 'failureThreshold': 60},
              'readinessProbe': {'tcpSocket': {'port': 27017}, 'periodSeconds': 15}}],
           'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': CLAIM}},
               {'name': 'secret', 'secret': {'secretName': 'mongo-membership', 'defaultMode': 292}},
               {'name': 'config', 'configMap': {'name': 'mongo-config'}}, {'name': 'runtime', 'emptyDir': {'medium': 'Memory'}}, {'name': 'tmp', 'emptyDir': {}}]}
    sts = object_('StatefulSet', 'mongo', {'replicas': 1, 'serviceName': 'mongo', 'podManagementPolicy': 'Parallel',
        'updateStrategy': {'type': 'OnDelete'}, 'selector': {'matchLabels': {'app': 'mongo-pilot'}},
        'template': {'metadata': {'labels': {'app': 'mongo-pilot', **OWNER}}, 'spec': pod}}, api='apps/v1')
    services = [object_('Service', 'mongo', {'clusterIP': 'None', 'publishNotReadyAddresses': True,
        'selector': {'app': 'mongo-pilot'}, 'ports': [{'port': 27017, 'targetPort': 27017}]})]
    services.append(object_('Service', 'mongo-access', {'selector': {'app': 'mongo-pilot'}, 'ports': [{'port': 27017, 'targetPort': 27017}]}))
    # Pods cannot reach vendor APIs. Only same-namespace Mongo traffic and cluster DNS leave them.
    # The sync controller has its own deny policy and must not inherit Mongo/DNS access.
    policy = object_('NetworkPolicy', 'mongo-isolation', {'podSelector': {'matchExpressions': [{'key':'chart-runtime','operator':'NotIn','values':['sync']}]}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [{'podSelector': {}}], 'ports': [{'port': 27017, 'protocol': 'TCP'}]}],
        'egress': [{'to': [{'podSelector': {}}], 'ports': [{'port': 27017, 'protocol': 'TCP'}]},
                   {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}, 'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
                    'ports': [{'port': 53, 'protocol': 'UDP'}, {'port': 53, 'protocol': 'TCP'}]}]}, api='networking.k8s.io/v1')
    return [ns, sc, pv, pvc, sec, cm, policy, *services, sts]


def shell(js, pod=0, auth=True, timeout=120):
    prefix = 'assert.commandWorked=(r)=>{assert.equal(r.ok,1);return r;};assert.eq=assert.equal;\n'
    if auth:
        c = json.loads((IDENTITY / 'credentials.json').read_text())
        prefix += 'db=db.getSiblingDB("admin");assert(db.auth(' + json.dumps(c['admin_user']) + ',' + json.dumps(c['admin_password']) + '));\n'
    p = subprocess.run(['kubectl', '-n', NS, 'exec', '-i', f'mongo-{pod}', '--', 'mongosh',
         'mongodb://localhost:27017/admin?directConnection=true',
         '--quiet', '--file', '/dev/stdin'], input=prefix + js, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        error = p.stderr[-1200:] + p.stdout[-1200:]
        c = json.loads((IDENTITY / 'credentials.json').read_text())
        for key in ('password', 'admin_password'):
            error = error.replace(c[key], '[redacted]')
        raise RuntimeError('mongosh failed: ' + error)
    return p.stdout.strip()


def cluster_status():
    for i in range(1):
        try:
            info = json.loads(shell('print(JSON.stringify(db.adminCommand({replSetGetStatus:1}).members.map(m=>({name:m.name,state:m.stateStr}))));', i))
            if len(info) == 1 and info[0]['state'] == 'PRIMARY':
                return info
        except (RuntimeError, ValueError):
            pass
    return None


def ready():
    for _ in range(45):
        s = cluster_status()
        if s:
            return s
        time.sleep(2)
    raise SystemExit('Single-member replica set did not become PRIMARY')


def bootstrap(inv):
    if not inv['initialized']:
        config = {'_id': 'rs0', 'members': [{'_id': 0, 'host': INT[0] + ':27017'}]}
        shell('try { db.adminCommand({replSetGetStatus:1}); } catch(e) { if(e.code===94) { assert.commandWorked(rs.initiate(' + json.dumps(config) + ')); } else if(e.code!==13) throw e; }', auth=False)
        c = json.loads((IDENTITY / 'credentials.json').read_text())
        for _ in range(60):
            primary = next((i for i in range(1) if 'true' == shell('print(db.hello().isWritablePrimary);', i, auth=False)), None)
            if primary is not None:
                break
            time.sleep(2)
        else:
            raise SystemExit('No primary after initiation')
        # Only this pilot has a new, locally generated administrator. Never reset existing users.
        try:
            shell('print("authenticated")', primary)
        except RuntimeError:
            shell('db=db.getSiblingDB("admin");db.createUser(' + json.dumps({'user': c['admin_user'], 'pwd': c['admin_password'], 'roles': ['root']}) + ');', primary, auth=False)
        shell('if(!db.getUser(' + json.dumps(c['user']) + ')) db.createUser(' + json.dumps({'user': c['user'], 'pwd': c['password'], 'roles': [{'role': 'readWrite', 'db': 'chart'}]}) + ');', primary)
        (DATA / 'default/member-0/.shared-dev-initialized').write_text(inv['markers'][0])
        inv['initialized'] = True
        write_json(IDENTITY / 'inventory.json', inv)
    s = ready()
    actual = json.loads(shell('print(JSON.stringify(rs.conf().members.map(m=>({host:m.host,horizons:m.horizons}))));'))
    expected = [{'host': INT[0] + ':27017'}]
    if actual != expected:
        raise SystemExit('Existing replica configuration differs; refusing automatic reconfiguration')
    marker = DATA / 'default/member-0/.shared-dev-initialized'
    if not marker.exists(): marker.write_text(inv['markers'][0])
    if marker.read_text() != inv['markers'][0]: raise SystemExit('Initialized marker differs')
    print(json.dumps(s))


def apply_owned(objects):
    for obj in objects:
        owned(kget(obj['kind'], obj['metadata']['name'], obj['metadata'].get('namespace')))
    apply(objects, dry=True)
    apply(objects)


def up():
    inv = inventory()
    host = load('host.json')['tailscale']
    ts = json.loads(run(['tailscale', 'status', '--json'], capture=True))
    if host not in ts.get('TailscaleIPs', []) or ts.get('BackendState') != 'Running':
        raise SystemExit('Registered Tailscale IP is not active')
    objs = manifests(inv)
    review = [{**o, 'data': {'redacted': 'private development material'}} if o['kind'] == 'Secret' else o for o in objs]
    save(f'profiles/{PROFILE}/manifest.review.json', {'apiVersion': 'v1', 'kind': 'List', 'items': review})
    apply_owned(objs[:1])
    apply_owned(objs[1:])
    run(['kubectl', '-n', NS, 'wait', '--for=jsonpath={.status.readyReplicas}=1', 'statefulset/mongo', '--timeout=240s'])
    for kind, name, ns, key in [('pv', PV, None, 'pv_uid'), ('pvc', CLAIM, NS, 'pvc_uid')]:
        inv[key] = kget(kind, name, ns)['metadata']['uid']
    write_json(IDENTITY / 'inventory.json', inv)
    bootstrap(inv)
    import access
    service = kget('service', 'mongo-access')
    access.set_route(PROFILE, {'namespace': NS, 'service': 'mongo-access', 'port': inv['port'], 'address': service['spec']['clusterIP']})
    connections(inv)
    print('Single-member replica set ready. Private Compass URI: ' + str(OUT / 'compass-uri.txt'))


def down(data_only=False):
    inventory()
    import access
    access.set_route(PROFILE, None)
    for kind, name, selector in [('statefulset', 'mongo', 'app=mongo-pilot')]:
        obj = kget(kind, name)
        owned(obj)
        if not obj:
            continue
        run(['kubectl', '-n', NS, 'scale', kind + '/' + name, '--replicas=0'])
        run(['kubectl', '-n', NS, 'wait', '--for=delete', 'pod', '-l', selector, '--timeout=150s'])
        if data_only:
            run(['kubectl', '-n', NS, 'delete', kind, name, '--wait=true'])
    if data_only:
        for obj in manifests(inventory())[4:-1]:
            kind, name = obj['kind'], obj['metadata']['name']
            owned(kget(kind, name))
            run(['kubectl', '-n', NS, 'delete', kind, name, '--ignore-not-found', '--wait=true'])
    print('Stopped. Retained all data, PV/PVC, disk identity and private configuration files.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'up', 'down', 'status', 'check'])
    p.add_argument('--profile', default='mongo-pilot-single')
    p.add_argument('--port', type=int, help='external port, prepare only')
    p.add_argument('--data-only', action='store_true')
    a = p.parse_args()
    if a.data_only and a.action != 'down': p.error('--data-only requires down')
    if a.port and a.action != 'prepare': p.error('--port requires prepare')
    configure(a.profile)
    guard()
    held = lock('chart-mongo-' + PROFILE)
    owned(kget('namespace', NS, None))
    if a.action == 'prepare':
        reservation = lock('chart-profiles')
        prepare(a.port)
    elif a.action == 'up': up()
    elif a.action == 'down': down(a.data_only)
    elif a.action == 'status':
        run(['kubectl', '-n', NS, 'get', 'statefulset,pod,pvc,service', '-o', 'wide'])
    elif a.action == 'check':
        import checks
        checks.check(__import__('__main__'))


if __name__ == '__main__':
    main()
