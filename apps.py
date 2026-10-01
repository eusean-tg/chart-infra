#!/usr/bin/env python3
"""One offline frontend-backend profile. Source/dependency preparation is explicit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

import access
import mongo
from common import STATE, guard, load, lock, run, save
from sources import valid_name

ROOT = Path(__file__).resolve().parent
SERVICES = {'auth': ('auth-service-backend',4001,'auth'), 'tharamine': ('tharamine-user-service',5001,'orange'), 'orange': ('orange-v2-backend',3000,'orangeV2')}
REDIS_IMAGE = 'docker.dragonflydb.io/dragonflydb/dragonfly@sha256:748447aa24ee7d14e28bb9bc2869dc62c1e14396bff6df8c83e8e9a6643eb65d'


def setup(profile):
    mongo.configure(profile)


def record():
    mongo.inventory()
    p = mongo.IDENTITY / 'apps/inventory.json'
    if not p.exists(): raise SystemExit('Run apps prepare first')
    inv = json.loads(p.read_text())
    for name, expected in inv.get('volume_uids', {}).items():
        kind, objname = name.split('/', 1)
        obj = mongo.kget(kind, objname, None if kind == 'pv' else 'profile')
        mongo.owned(obj)
        if not obj or obj['metadata']['uid'] != expected:
            raise SystemExit('Application volume missing/replaced; refusing empty replacement')
    return inv


def prepare(workspace, port):
    inv = mongo.inventory()
    port = port or {'sean':13000,'alex':13001,'ryan':13002,'gerald':13003}.get(mongo.PROFILE)
    if not port or not 1024 <= port <= 65535: raise SystemExit('Supply an unused --port between 1024 and 65535')
    base = mongo.IDENTITY / 'apps'
    if base.exists():
        old = record()
        if old['workspace'] != workspace or old['api_port'] != port:
            raise SystemExit('Existing application selection differs; refusing implicit switch')
        print('Retained app identities/config reused.'); return
    sources = load(f'sources/{mongo.PROFILE}/{workspace}.json')
    deps = {svc: load(f'dependencies/{mongo.PROFILE}/{workspace}/{repo}.json') for svc,(repo,_,_) in SERVICES.items()}
    image_ids = {d['image'] for d in deps.values()}
    if len(image_ids) != 1: raise SystemExit('All app dependencies must use the same runtime image')
    for other in Path('/mnt/hdd/shared-dev/profiles').glob('*/identity/apps/inventory.json'):
        if json.loads(other.read_text())['api_port'] == port: raise SystemExit('API port already reserved by another profile')
    import socket
    with socket.socket() as sock: sock.bind((load('host.json')['tailscale'], port))
    image = load('node-runtime.json')['cluster_image']
    if load('node-runtime.json')['docker_image'] not in image_ids: raise SystemExit('Runtime image differs from dependency ABI')
    base.mkdir(mode=0o700)
    keydir = base/'issuer';keydir.mkdir(mode=0o700)
    def keypair(directory, ec=False):
        directory.mkdir(exist_ok=True,mode=0o700)
        subprocess.run(['openssl','genpkey','-algorithm','EC' if ec else 'RSA','-pkeyopt','ec_paramgen_curve:P-256' if ec else 'rsa_keygen_bits:2048','-out',str(directory/'private.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['openssl','pkey','-in',str(directory/'private.pem'),'-pubout','-out',str(directory/'public.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return (directory/'private.pem').read_text(), (directory/'public.pem').read_text()
    user_private,user_public = keypair(keydir)
    keyset = [{'kid':'local-key-1','kms_key_id':None,'created_at':'2026-10-01T00:00:00Z','rotated_at':None,'sign_algo':'RSASSA_PKCS1_V1_5_SHA_256','public_key':user_public}]
    services={};registry=[]
    for svc,name in [('orange','orange_v2_backend'),('tharamine','tharamine-user-service')]:
        private,public=keypair(base/svc, ec=svc=='tharamine')
        services[svc]=private
        registry.append({'service':name,'keys':[{'kid':'chart-dev-1','created_at':'2026-10-01T00:00:00Z','sign_algo':'ES256' if svc=='tharamine' else 'RS256','public_key':public}]})
    mongo_users={}
    backend_key=secrets.token_hex(32)
    redis_password=secrets.token_hex(32)
    for svc,(repo,svcport,db) in SERVICES.items():
        directory=base/svc;directory.mkdir(exist_ok=True,mode=0o700)
        credential={'user':'chart_'+svc,'password':secrets.token_hex(24),'database':db}
        mongo_users[svc]=credential
        uri=f'mongodb://{credential["user"]}:{credential["password"]}@{mongo.INT[0]}:27017/{db}?authSource=admin&replicaSet=rs0'
        config={'NODE_ENV':'development','PORT':str(svcport),'MONGO_URI':uri,'MONGO_DB_NAME':db,
                'OTEL_SDK_DISABLED':'true','OTEL_TRACES_EXPORTER':'none','OTEL_METRICS_EXPORTER':'none','OTEL_LOGS_EXPORTER':'none',
                'SENTRY_ENABLED':'false','METRICS_ENABLED':'false','AWS_EC2_METADATA_DISABLED':'true',
                'AWS_REGION':'us-east-1','AWS_ACCESS_KEY_ID':'offline-local','AWS_SECRET_ACCESS_KEY':'offline-local',
                'AWS_ENDPOINT_URL':'http://127.0.0.1:9','AWS_ENDPOINT_URL_SES':'http://127.0.0.1:9','AWS_MAX_ATTEMPTS':'1',
                'JWT_DEFAULT_KEY':'local-key-1','JWT_KEYS':json.dumps(keyset),'API_BACKEND_KEY':backend_key,
                'AUTH_SERVICE_URL':'http://auth:4001','USER_SERVICE_URL':'http://tharamine:5001',
                'FRONTEND_URL':'http://localhost:8080/chart','FRONTEND_EXTERNAL_DOMAIN':'http://localhost:8080/chart',
                'CORS_ORIGINS':'http://localhost:8080,http://127.0.0.1:8080,http://localhost:8097,http://127.0.0.1:8097',
                'COOKIE_DOMAIN':'','THARAMINE_COOKIE_DOMAIN':'','OPENMARKET_COOKIE_DOMAIN':'',
                'REDIS_URL':f'redis://:{redis_password}@redis:6379',
                'EMAIL_PROVIDER':'ses','TWOFA_ENABLED':'false'}
        if svc=='auth':
            (directory/'private.pem').write_text(user_private);(directory/'public.pem').write_text(user_public)
            config.update({'PRIVATE_KEY_PATH':'/run/chart/private.pem','PUBLIC_KEY_PATH':'/run/chart/public.pem',
                           'JWT_PRIVATE_KEY':user_private,'AWS_KMS_KEYS':json.dumps(keyset),'ALGORITHM':'RS256',
                           'SERVICE_AUTH_KEYS':json.dumps(registry),'SESSION_SECRET':secrets.token_hex(32),'TWOFA_ENCRYPT_KEY':secrets.token_hex(32)})
        elif svc=='orange':
            config.update({'SERVICE_AUTH_NAME':'orange_v2_backend','SERVICE_AUTH_KID':'chart-dev-1','SERVICE_SIGN_ALGO':'RS256','SERVICE_AUTH_KEY':services[svc],
                           'MARKETPLACE_OUTBOX_RUNNER_ENABLED':'false','MARKET_EVENTS_INGEST_ENABLED':'false','KSCRIPT_ALERT_ENTITLEMENT_RECONCILE_MS':'0',
                           'ENTITLEMENT_SERVICE_URL':'http://127.0.0.1:9','INTERNAL_SERVICE_KEY':backend_key})
        else:
            config.update({'SERVICE_AUTH_PRIVATE_KEY':services[svc],'SERVICE_AUTH_KID':'chart-dev-1',
                           'EMAIL_FOLLOWER_FANOUT_ENABLED':'false',
                           'OM_ROOM_NOTIFY_ENABLED':'false','OM_ROOM_UNFURL_ENABLED':'false','VOICE_ENABLED':'false',
                           'ROOMS_CANVAS_CHECKPOINT_V1_STORE':'false','KSCRIPT_ALERT_RECONCILE_MS':'0','INTERNAL_SERVICE_KEY':backend_key})
        (directory/'config.json').write_text(json.dumps(config))
        (directory/'launch.cjs').write_bytes((ROOT/'runtime/launch.cjs').read_bytes())
    prepare_backend_key(base/'auth')
    rdir=base/'redis';rdir.mkdir();(rdir/'password').write_text(redis_password)
    mongo.write_json(base/'database-users.json',mongo_users)
    mongo.write_json(base/'inventory.json',{'workspace':workspace,'api_port':port,'sources':sources,'deps':deps,'image':image,'profile':mongo.PROFILE})
    print('Prepared retained development-only app identities/configuration.')


def prepare_backend_key(directory):
    """Explicit, additive development identity provisioning; never rotate a key."""
    directory = Path(directory)
    if directory.resolve() != directory:
        raise SystemExit('Refusing symlinked auth configuration')
    config_path = directory/'config.json'
    private = directory/'backend-private.pem'
    public = directory/'backend-public.pem'
    for path in (config_path, private, public):
        if path.is_symlink():
            raise SystemExit('Refusing symlinked auth identity file')
    config = json.loads(config_path.read_text())
    expected = '/run/chart/backend-private.pem'
    if config.get('ALGORITHM') != 'RS256':
        raise SystemExit('Backend key provisioning expects the development RS256 config')
    if config.get('PRIVATE_KEY_PATH_BACKEND') not in (None, '', expected):
        raise SystemExit('Existing backend key path differs; refusing replacement')
    if not private.exists():
        if config.get('PRIVATE_KEY_PATH_BACKEND') or public.exists():
            raise SystemExit('Retained backend private key missing; refusing regeneration')
        key = subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048'],
                             check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL).stdout
        with private.open('xb') as output:
            output.write(key)
        private.chmod(0o600)
    if private.stat().st_mode & 0o077:
        raise SystemExit('Backend private key must be owner-only; inspect permissions')
    derived = subprocess.run(['openssl','pkey','-in',str(private),'-pubout'],
                             check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL).stdout
    if public.exists():
        if public.read_bytes() != derived:
            raise SystemExit('Backend public key differs; refusing replacement')
    else:
        with public.open('xb') as output:
            output.write(derived)
        public.chmod(0o600)
    if config.get('PRIVATE_KEY_PATH_BACKEND') != expected:
        backup = directory.parent/('auth-config-before-backend-key-'+str(time.time_ns())+'.json')
        with backup.open('xb') as output:
            output.write(config_path.read_bytes())
        config['PRIVATE_KEY_PATH_BACKEND'] = expected
        mongo.write_json(config_path, config)
    print('Retained development backend key ready; restart auth to load changed config.')


def storage(name,path):
    pv=mongo.NS+'-'+name
    return [mongo.object_('PersistentVolume',pv,{'capacity':{'storage':'10Gi'},'accessModes':['ReadWriteOnce'],'persistentVolumeReclaimPolicy':'Retain',
       'storageClassName':mongo.PV,'local':{'path':str(path)},'claimRef':{'namespace':mongo.NS,'name':name},
       'nodeAffinity':{'required':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':[load('host.json')['node']]}]}]}}},namespace=False),
       mongo.object_('PersistentVolumeClaim',name,{'accessModes':['ReadWriteOnce'],'storageClassName':mongo.PV,'volumeName':pv,'resources':{'requests':{'storage':'10Gi'}}})]


def service(name,port):
    return mongo.object_('Service',name,{'selector':{'chart-app':name},'ports':[{'port':port,'targetPort':port}]})


def podsecurity():
    return {'runAsNonRoot':True,'runAsUser':os.getuid(),'runAsGroup':os.getgid(),'seccompProfile':{'type':'RuntimeDefault'}}


def containersecurity():
    return {'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}


def manifests(inv):
    objects=[]
    for svc,(repo,port,_) in SERVICES.items():
        src=Path(inv['sources'][repo]['path']);dep=Path(inv['deps'][svc]['modules']);config=Path(inv.get('config_paths',{}).get(svc,mongo.IDENTITY/'apps'/svc))
        claims=inv.get('claims',{}).get(svc,{name:svc+'-'+name for name in ('source','deps','config')})
        for name,path in [(claims['source'],src),(claims['deps'],dep),(claims['config'],config)]:
            if path.resolve()!=path or not path.is_dir(): raise SystemExit('Missing or symlinked app volume')
            objects.extend(storage(name,path))
        volumes=[{'name':name,'persistentVolumeClaim':{'claimName':claims[name]}} for name in ['source','deps','config']]+[{'name':'tmp','emptyDir':{}}]
        mounts=[{'name':'source','mountPath':'/app','readOnly':True},{'name':'deps','mountPath':'/app/node_modules','readOnly':True},
                {'name':'config','mountPath':'/run/chart','readOnly':True},{'name':'tmp','mountPath':'/tmp'}]
        container={'name':svc,'image':inv['image'],'workingDir':'/app','command':['node','/run/chart/launch.cjs'],
                   'securityContext':containersecurity(),'volumeMounts':mounts,'ports':[{'containerPort':port}],
                   'startupProbe':{'tcpSocket':{'port':port},'periodSeconds':2,'failureThreshold':90},
                   'readinessProbe':{'httpGet':{'path':'/api/v1/health/services' if svc=='orange' else '/api/v2/health','port':port},'periodSeconds':3,'failureThreshold':1}}
        spec={'replicas':1,'strategy':{'type':'Recreate'},'selector':{'matchLabels':{'chart-app':svc}},
              'template':{'metadata':{'labels':{'chart-app':svc,'chart-runtime':'apps'}},'spec':{'automountServiceAccountToken':False,
                'securityContext':podsecurity(),'terminationGracePeriodSeconds':30,'containers':[container],'volumes':volumes}}}
        objects+=[mongo.object_('Deployment',svc,spec,api='apps/v1'),service(svc,port)]
    objects.extend(storage('redis-config',mongo.IDENTITY/'apps/redis'))
    c={'name':'redis','image':REDIS_IMAGE,'command':['sh','-c','exec dragonfly --logtostderr --cache_mode=true --proactor_threads=1 --requirepass="$(cat /run/chart/password)"'],
       'securityContext':containersecurity(),'volumeMounts':[{'name':'config','mountPath':'/run/chart','readOnly':True},{'name':'tmp','mountPath':'/tmp'}],
       'readinessProbe':{'tcpSocket':{'port':6379},'periodSeconds':2}}
    objects += [mongo.object_('Deployment','redis',{'replicas':1,'strategy':{'type':'Recreate'},'selector':{'matchLabels':{'chart-app':'redis'}},
                'template':{'metadata':{'labels':{'chart-app':'redis','chart-runtime':'apps'}},'spec':{'automountServiceAccountToken':False,
                'securityContext':podsecurity(),'containers':[c],'volumes':[{'name':'config','persistentVolumeClaim':{'claimName':'redis-config'}},{'name':'tmp','emptyDir':{}}]}}},api='apps/v1'),service('redis',6379)]
    objects += [mongo.object_('NetworkPolicy','chart-app-isolation',{'podSelector':{'matchLabels':{'chart-runtime':'apps'}},'policyTypes':['Ingress','Egress'],
       'ingress':[{'from':[{'podSelector':{'matchLabels':{'chart-runtime':'apps'}}}],'ports':[{'port':p,'protocol':'TCP'} for p in [3000,4001,5001,6379]]}],
       'egress':[{'to':[{'podSelector':{'matchLabels':{'chart-runtime':'apps'}}}],'ports':[{'port':p,'protocol':'TCP'} for p in [3000,4001,5001,6379]]},
                 {'to':[{'podSelector':{'matchLabels':{'app':'mongo-pilot'}}}],'ports':[{'port':27017,'protocol':'TCP'}]},
                 {'to':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'kube-system'}},'podSelector':{'matchLabels':{'k8s-app':'kube-dns'}}}],
                  'ports':[{'port':53,'protocol':'UDP'},{'port':53,'protocol':'TCP'}]}]},api='networking.k8s.io/v1'),
                mongo.object_('NetworkPolicy','mongo-from-chart-apps',{'podSelector':{'matchLabels':{'app':'mongo-pilot'}},'policyTypes':['Ingress'],
                  'ingress':[{'from':[{'podSelector':{'matchLabels':{'chart-runtime':'apps'}}}],'ports':[{'port':27017,'protocol':'TCP'}]}]},api='networking.k8s.io/v1')]
    return objects


def validate_sources(inv):
    for svc,(repo,_,_) in SERVICES.items():
        source = Path(inv['sources'][repo]['path'])
        h = hashlib.sha256(inv['deps'][svc]['image'].encode())
        for name in ['package.json','pnpm-lock.yaml','pnpm-workspace.yaml','.pnpmfile.cjs']:
            f = source/name
            if f.exists(): h.update(name.encode()+b'\0'+f.read_bytes())
        if h.hexdigest() != inv['deps'][svc]['key']:
            raise SystemExit(f'{repo}: dependency inputs changed; explicitly install dependencies and select the prepared workspace before starting')


def stock_tharamine_config():
    """Retain the pilot config; give synced stock source a separate config PV."""
    original = mongo.IDENTITY/'apps/tharamine'
    if original.resolve() != original:
        raise SystemExit('Refusing symlinked application configuration')
    contents = {}
    for file in original.iterdir():
        if file.is_symlink() or not file.is_file():
            raise SystemExit('Unexpected entry in application configuration')
        contents[file.name] = file.read_bytes()
    config = json.loads(contents['config.json'])
    for name in ('LOCAL_BACKGROUND_WORKERS_DISABLED','MONGO_WAIT_QUEUE_TIMEOUT_MS'):
        config.pop(name, None)
    contents['config.json'] = (json.dumps(config, sort_keys=True)+'\n').encode()
    h = hashlib.sha256()
    for name, data in sorted(contents.items()):
        h.update(name.encode()+b'\0'+data+b'\0')
    target = mongo.IDENTITY/'apps/runtime-configs'/('tharamine-'+h.hexdigest())
    if target.resolve() != target:
        raise SystemExit('Refusing symlinked stock configuration')
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never overwrite a retained generation, including a failed partial one.
        target.mkdir(mode=0o700)
        for name, data in contents.items():
            (target/name).write_bytes(data)
            (target/name).chmod(0o600)
    if {p.name:p.read_bytes() for p in target.iterdir() if p.is_file() and not p.is_symlink()} != contents or len(list(target.iterdir())) != len(contents):
        raise SystemExit('Retained stock configuration differs; inspect before selection')
    return target


def select(workspace):
    """Explicit selection only; never fetch, install or start applications."""
    inv=record()
    for svc in (*SERVICES,'redis'):
        obj=mongo.kget('deployment',svc);mongo.owned(obj)
        if obj and (obj['spec'].get('replicas',0) or obj.get('status',{}).get('replicas',0)):
            raise SystemExit('Stop apps before selecting source/dependencies')
    sources=load(f'sources/{mongo.PROFILE}/{workspace}.json')
    if any(s.get('sync') for s in sources.values()):
        import sync as sync_runtime
        sync_runtime.assert_selection(workspace,sources)
    deps={svc:load(f'dependencies/{mongo.PROFILE}/{workspace}/{repo}.json') for svc,(repo,_,_) in SERVICES.items()}
    runtime=load('node-runtime.json')
    if any(d['image']!=runtime['docker_image'] for d in deps.values()):raise SystemExit('Prepared dependency runtime differs')
    candidate={**inv,'workspace':workspace,'sources':sources,'deps':deps,'image':runtime['cluster_image'],'claims':{},'config_paths':{}}
    for svc,(repo,_,_) in SERVICES.items():
        src=sources[repo];path=Path(src['path'])
        if src.get('sync'):
            from sync_common import fingerprint,manifest
            if fingerprint(manifest(path))!=src['fingerprint']:raise SystemExit('Mirror changed since laptop checkpoint; wait and pause sync before selecting')
        source_claim=src.get('claim') or svc+'-src-'+hashlib.sha256(str(path).encode()).hexdigest()[:12]
        candidate['claims'][svc]={'source':source_claim,'deps':svc+'-dep-'+hashlib.sha256(deps[svc]['modules'].encode()).hexdigest()[:12],'config':svc+'-config'}
    validate_sources(candidate)
    if sources['tharamine-user-service'].get('sync'):
        config = stock_tharamine_config()
        candidate['config_paths']['tharamine'] = str(config)
        candidate['claims']['tharamine']['config'] = 'tharamine-cfg-'+hashlib.sha256(str(config).encode()).hexdigest()[:12]
    # All previous claims remain in the identity guard; none are rebound or deleted.
    stamp=str(time.time_ns())
    mongo.write_json(mongo.IDENTITY/'apps'/('selection-before-'+stamp+'.json'),inv)
    mongo.write_json(mongo.IDENTITY/'apps/inventory.json',candidate)
    print('Selected prepared workspace '+workspace+'. Original checkouts, volumes and identities retained; apps remain stopped.')


def up():
    inv=record();validate_sources(inv)
    if not mongo.ready(): raise SystemExit('Start this profile Mongo first')
    users=json.loads((mongo.IDENTITY/'apps/database-users.json').read_text())
    for svc,c in users.items():
        mongo.shell('const c='+json.dumps(c)+'; const a=db.getSiblingDB("admin"); if(!a.getUser(c.user)){a.createUser({user:c.user,pwd:c.password,roles:[{role:"readWrite",db:c.database}]});} else {assert(a.auth(c.user,c.password));}')
    objects=manifests(inv)
    # Networking is applied before creating any app Pod.
    mongo.apply_owned([o for o in objects if o['kind']=='NetworkPolicy'])
    mongo.apply_owned([o for o in objects if o['kind'] in ['PersistentVolume','PersistentVolumeClaim','Service']])
    inv.setdefault('volume_uids',{})
    for o in objects:
        if o['kind'] in ('PersistentVolume','PersistentVolumeClaim'):
            kind='pv' if o['kind']=='PersistentVolume' else 'pvc';name=o['metadata']['name']
            inv['volume_uids'][kind+'/'+name]=mongo.kget(kind,name,None if kind=='pv' else 'profile')['metadata']['uid']
    mongo.write_json(mongo.IDENTITY/'apps/inventory.json',inv)
    for svc in ['redis','auth','tharamine','orange']:
        mongo.apply_owned([o for o in objects if o['kind']=='Deployment' and o['metadata']['name']==svc])
        run(['kubectl','-n',mongo.NS,'rollout','status','deployment/'+svc,'--timeout=180s'])
    address=mongo.kget('service','orange')['spec']['clusterIP']
    access.set_route('api-'+mongo.PROFILE,{'port':inv['api_port'],'address':address,'target_port':3000})
    save(f'profiles/{mongo.PROFILE}/apps-manifest.json',objects)
    for svc,(_,_,db) in SERVICES.items():
        c=users[svc]
        uri=f'mongodb://{c["user"]}:{c["password"]}@{load("host.json")["tailscale"]}:{mongo.inventory()["port"]}/{db}?authSource=admin&directConnection=true'
        (mongo.OUT/f'compass-{svc}-uri.txt').write_text(uri+'\n')
    print(f'API: http://{load("host.json")["tailscale"]}:{inv["api_port"]}; Tailscale only, intended for laptop Vite proxy')


def down(data_only=False):
    inv=record()
    if data_only:
        pods=json.loads(run(['kubectl','-n',mongo.NS,'get','pods','-l','chart-runtime=apps','-o','json'],capture=True))['items']
        extra=[p['metadata']['name'] for p in pods if p['status']['phase'] not in ('Succeeded','Failed') and p['metadata']['labels'].get('chart-app') not in (*SERVICES,'redis')]
        if extra: raise SystemExit('Stop verification/extra app Pods before removing network policies: '+', '.join(extra))
    access.set_route('api-'+mongo.PROFILE,None)
    for svc in ['orange','tharamine','auth','redis']:
        obj=mongo.kget('deployment',svc);mongo.owned(obj)
        if obj: run(['kubectl','-n',mongo.NS,'scale','deployment/'+svc,'--replicas=0'])
    for svc in ['orange','tharamine','auth','redis']:
        run(['kubectl','-n',mongo.NS,'wait','--for=delete','pod','-l','chart-app='+svc,'--timeout=90s'])
    if data_only:
        for o in manifests(inv):
            if o['kind'] in ['Deployment','Service','NetworkPolicy']:
                old=mongo.kget(o['kind'],o['metadata']['name']);mongo.owned(old)
                if old: run(['kubectl','-n',mongo.NS,'delete',o['kind'],o['metadata']['name']])
    print('Apps stopped. All data, source, dependency caches, volumes and identities retained.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','prepare-backend-key','up','down','status','logs','restart','login-code','select'])
    p.add_argument('--profile',type=valid_name,default='sean');p.add_argument('--workspace',type=valid_name,default='pilot')
    p.add_argument('--port',type=int);p.add_argument('--data-only',action='store_true')
    p.add_argument('--service',choices=list(SERVICES)+['redis'],default='orange')
    p.add_argument('--email',help='Synthetic .test email, login-code only')
    a=p.parse_args()
    if a.data_only and a.action!='down': p.error('--data-only is only valid for down')
    if a.port is not None and a.action!='prepare': p.error('--port is only valid for prepare')
    if a.email and a.action!='login-code': p.error('--email is only valid for login-code')
    guard();held=lock('chart-apps-'+a.profile);mongo_held=lock('chart-mongo-'+a.profile);setup(a.profile)
    reservation=lock('chart-profiles') if a.action=='prepare' else None
    if a.action=='prepare':prepare(a.workspace,a.port)
    elif a.action=='prepare-backend-key':
        inv=record()
        directory=mongo.IDENTITY/'apps/auth'
        if Path(inv.get('config_paths',{}).get('auth',directory)) != directory:
            raise SystemExit('Selected auth config differs; inspect before identity provisioning')
        prepare_backend_key(directory)
    elif a.action=='select':select(a.workspace)
    elif a.action=='up':up()
    elif a.action=='down':down(a.data_only)
    elif a.action=='status':run(['kubectl','-n',mongo.NS,'get','pods,svc,pvc'])
    elif a.action=='logs':run(['kubectl','-n',mongo.NS,'logs','deployment/'+a.service,'--tail=80'])
    elif a.action=='login-code':
        import re
        if not a.email or not a.email.lower().endswith('.test'):
            raise SystemExit('Supply the synthetic .test email used in the browser')
        logs=run(['kubectl','-n',mongo.NS,'logs','deployment/auth','--since=10m'],capture=True)
        matches=re.findall(r'sign-in code for '+re.escape(a.email.lower())+r': (\d{6})',logs)
        if not matches: raise SystemExit('No recent code found; request a new one in the browser')
        print(matches[-1])
    elif a.action=='restart':
        mongo.owned(mongo.kget('deployment',a.service));run(['kubectl','-n',mongo.NS,'rollout','restart','deployment/'+a.service]);run(['kubectl','-n',mongo.NS,'rollout','status','deployment/'+a.service,'--timeout=180s'])

if __name__=='__main__': main()
