#!/usr/bin/env python3
"""Real Mutagen/SSH fixture on the PC; never claims Mac client acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import socket
import tempfile
import time
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import guard,save,load,run
import apps,mongo
from sync_common import REPOS,VERSION,policy_hash,manifest,fingerprint,sessions,mutagen
spec=importlib.util.spec_from_file_location('laptop',Path(__file__).resolve().parents[1]/'laptop-sync.py')
laptop=importlib.util.module_from_spec(spec);spec.loader.exec_module(laptop)

def ssh_fixture():
 p=Path(tempfile.mkdtemp(prefix='chart-mutagen-ssh-'))
 for name in ('host','client'):
  subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(p/name)],check=True)
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 # Test server only. StrictModes=no permits its private 0700 directory beneath /tmp.
 # Existing sshd, accounts and authorized_keys are never modified.
 (p/'sshd_config').write_text(f'Port {port}\nListenAddress 127.0.0.1\nHostKey {p}/host\nPidFile {p}/sshd.pid\nAuthorizedKeysFile {p}/client.pub\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\nStrictModes no\nAllowUsers sean\nSubsystem sftp internal-sftp\n')
 (p/'known_hosts').write_text('[127.0.0.1]:'+str(port)+' '+(p/'host.pub').read_text())
 (p/'ssh_config').write_text(f'Host chart-mutagen-fixture\n HostName 127.0.0.1\n User sean\n Port {port}\n IdentityFile {p}/client\n IdentitiesOnly yes\n UserKnownHostsFile {p}/known_hosts\n StrictHostKeyChecking yes\n BatchMode yes\n')
 (p/'bin').mkdir()
 for tool in ('ssh','scp'):
  f=p/'bin'/tool;f.write_text(f'#!/bin/sh\nexec /usr/bin/{tool} -F {p}/ssh_config "$@"\n');f.chmod(0o700)
 with (p/'sshd.log').open('w') as log:
  proc=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(p/'sshd_config')],stdout=log,stderr=log)
 for _ in range(30):
  with socket.socket() as sock:
   if sock.connect_ex(('127.0.0.1',port))==0:return p,proc
  if proc.poll() is not None:raise RuntimeError('Fixture SSH server failed: '+str(p/'sshd.log'))
  time.sleep(.1)
 proc.terminate();proc.wait();raise RuntimeError('Fixture SSH server timeout')

def main():
 guard();mongo.configure('sean');mongo.inventory()
 stamp=str(time.time_ns())[-12:]
 root=Path('/mnt/hdd/shared-dev/verification')/('mutagen-'+stamp)
 if root.exists():raise SystemExit('Fixture already exists; retain and inspect before a separate run')
 root.mkdir(parents=True,mode=0o700)
 ssh_root,sshd=ssh_fixture()
 os.environ['MUTAGEN_DATA_DIRECTORY']=str(ssh_root/'daemon')
 os.environ['MUTAGEN_SSH_PATH']=str(ssh_root/'bin')
 binary=str(Path.home()/'.local/share/chart-infra/tools/mutagen/0.18.1/mutagen')
 paths={};folders={};result={'mutagen_version':VERSION,'transport':'PC loopback SSH fixture','not_mac_acceptance':True,'fixture_root':str(root)}
 for svc,repo in REPOS.items():
  a=root/'laptop'/repo;b=root/'mirrors'/repo;(a/'src').mkdir(parents=True);b.mkdir(parents=True)
  (a/'package.json').write_text('{"type":"module"}')
  (a/'pnpm-lock.yaml').write_text('fixture-lock')
  (a/'src/index.ts').write_text('console.log("initial-'+svc+'"); setInterval(()=>{},1000);\n')
  (a/'AGENTS.md').write_text('fixture');(a/'CLAUDE.md').symlink_to('AGENTS.md')
  (a/'node_modules').mkdir();(a/'node_modules/WRONG_PLATFORM').write_text('fixture')
  for n in ['.env.local','.EnV.LoCaL','.npmrc','.NETRC','private.pem','private.PeM']:(a/n).write_text('SYNTHETIC_EXCLUSION_PROBE')
  for parent in (a,a/'src'):
   for n in ('graphify-out','.HuSkY'):
    (parent/n).mkdir();(parent/n/'generated.json').write_text('LOCAL_TOOL_FIXTURE')
   (parent/'.GraphifyIgnore').write_text('LOCAL_IGNORE_FIXTURE')
  (a/'.env.sample').write_text('EMPTY=');(a/'src/.env.sample').write_text('EXCLUDED_NESTED')
  (b/'node_modules').mkdir();(b/'.chart-sync-root').write_text('fixture-marker')
  subprocess.run(['git','init','-q',str(a)],check=True)
  subprocess.run(['git','-C',str(a),'add','package.json','pnpm-lock.yaml','src/index.ts','AGENTS.md'],check=True)
  subprocess.run(['git','-C',str(a),'-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','fixture'],check=True)
  paths[svc]=str(a);folders[svc]={'repo':repo,'host_path':str(b),'claim':'mutagen-'+stamp+'-'+svc}
 exp={'transport':'mutagen-ssh','profile':'sean','workspace':'fixture','uid':os.getuid(),'gid':os.getgid(),'owner':'sean',
      'registration':'fixture','policy_hash':policy_hash(),'version':VERSION,'folders':folders}
 statefile=root/'client/sessions.json';statefile.parent.mkdir()
 args=SimpleNamespace(host='sean@chart-mutagen-fixture',remote_dir='/unused',profile='fixture',mutagen=binary,timeout=30,
                      syncthing_config=[],**paths)
 registered={};checkpoints=[]
 def remote(args,action,payload=None):
  if action=='export':return exp
  if action=='register':registered.update(payload);return {'registered':True}
  if action=='invalidate':return {'frozen':False}
  if action=='checkpoint':
   for svc,f in folders.items():
    assert fingerprint(manifest(f['host_path']))==payload['repos'][svc]['fingerprint'],svc+' differs'
   checkpoints.append(payload);return {'verified':True}
  raise AssertionError(action)
 laptop.remote=remote
 resources=[];pod_name='chart-mutagen-fixture'
 try:
  # Simulate interruption after session creation, before local ID persistence.
  original_write=laptop.write_json;interrupted=[False]
  def interrupted_write(path,state):
   if state.get('sessions') and not interrupted[0]:interrupted[0]=True;raise RuntimeError('simulated interruption')
   original_write(path,state)
  laptop.write_json=interrupted_write
  try:laptop.setup(args,None,statefile)
  except RuntimeError:pass
  finally:laptop.write_json=original_write
  assert interrupted[0]
  saved=json.loads(statefile.read_text());laptop.setup(args,saved,statefile)
  saved=json.loads(statefile.read_text());ids=saved['sessions'].copy();laptop.setup(args,saved,statefile)
  assert json.loads(statefile.read_text())['sessions']==ids
  result['interrupted_and_repeated_setup']=True
  laptop.change(args,saved,'resume');laptop.checkpoint(args,saved,False)
  result['ssh_initial_transfer_and_fingerprints']=True
  for svc,f in folders.items():
   b=Path(f['host_path']);assert (b/'node_modules').is_dir();assert (b/'.chart-sync-root').read_text()=='fixture-marker'
   for n in ['.env.local','.EnV.LoCaL','.npmrc','.NETRC','private.pem','private.PeM','CLAUDE.md','src/.env.sample']:
    assert not (b/n).exists(),n
   assert not (b/'node_modules/WRONG_PLATFORM').exists();assert (b/'.env.sample').exists()
   for parent in (b,b/'src'):
    for n in ('graphify-out','.HuSkY','.GraphifyIgnore'):assert not (parent/n).exists(),n
  result['exclusions_and_mountpoint_retention']=True
  # A real Pod mounts each HDD mirror read-only and runs the installed tsx watcher.
  app=apps.record();volumes=[];containers=[]
  for svc,f in folders.items():
   resources+=apps.storage(f['claim'],Path(f['host_path']))
   volumes.extend([{'name':svc,'persistentVolumeClaim':{'claimName':f['claim']}},
                   {'name':svc+'-deps','persistentVolumeClaim':{'claimName':app['claims'][svc]['deps']}}])
   containers.append({'name':svc,'image':app['image'],'workingDir':'/app',
     'command':['node','/app/node_modules/tsx/dist/cli.mjs','watch','src/index.ts'],
     'securityContext':apps.containersecurity(),'env':[{'name':'HOME','value':'/tmp'}],
     'volumeMounts':[{'name':svc,'mountPath':'/app','readOnly':True},
                    {'name':svc+'-deps','mountPath':'/app/node_modules','readOnly':True},
                    {'name':'tmp','mountPath':'/tmp'}]})
  volumes.append({'name':'tmp','emptyDir':{}})
  policy=mongo.object_('NetworkPolicy','chart-mutagen-fixture',{'podSelector':{'matchLabels':{'chart-runtime':'sync'}},'policyTypes':['Ingress','Egress'],'ingress':[],'egress':[]},api='networking.k8s.io/v1')
  resources.append(policy);mongo.apply_owned(resources)
  pod={'apiVersion':'v1','kind':'Pod','metadata':mongo.meta(pod_name),'spec':{'automountServiceAccountToken':False,'restartPolicy':'Never',
       'securityContext':apps.podsecurity(),'containers':containers,'volumes':volumes}}
  pod['metadata']['labels']['chart-runtime']='sync';mongo.apply_owned([pod])
  run(['kubectl','-n',mongo.NS,'wait','--for=condition=ready','pod/'+pod_name,'--timeout=120s'])
  before=mongo.kget('pod',pod_name)
  for svc in REPOS:
   a=Path(paths[svc]);target=a/'src/index.ts';temp=a/'src/.edit.tmp'
   marker='mutagen-watcher-'+svc+'-'+str(time.time_ns())
   temp.write_text('console.log('+json.dumps(marker)+'); setInterval(()=>{},1000);\n');temp.replace(target)
   (a/'src/created.ts').write_text('created')
  laptop.checkpoint(args,saved,False)
  for svc in REPOS:
   marker=(Path(paths[svc])/'src/index.ts').read_text().split('"')[1]
   deadline=time.monotonic()+60
   while time.monotonic()<deadline:
    logs=run(['kubectl','-n',mongo.NS,'logs',pod_name,'-c',svc],capture=True)
    if marker in logs:break
    time.sleep(1)
   else:raise AssertionError('Watcher failed '+svc)
  after=mongo.kget('pod',pod_name);assert after['metadata']['uid']==before['metadata']['uid'];assert after['spec']['containers']==before['spec']['containers']
  result['atomic_save_three_pod_watchers']=True
  for svc in REPOS:
   a=Path(paths[svc]);(a/'src/created.ts').rename(a/'src/renamed.ts')
  laptop.checkpoint(args,saved,False)
  for svc in REPOS:(Path(paths[svc])/'src/renamed.ts').unlink()
  laptop.checkpoint(args,saved,False);result['create_rename_delete']=True
  # Stop/restart only this isolated fixture daemon; owned sessions survive.
  mutagen(binary,'daemon','stop');mutagen(binary,'daemon','start')
  laptop.checkpoint(args,saved,False);result['daemon_reconnect']=True
  # one-way-safe does not erase a nonconflicting PC-only file; fingerprint catches it.
  extra=Path(folders['auth']['host_path'])/'extra.ts';extra.write_text('PC-only fixture')
  try:
   try:laptop.checkpoint(args,saved,False)
   except AssertionError:pass
   else:raise AssertionError('Extra PC file falsely accepted')
  finally:extra.unlink() # Only this test's synthetic probe.
  # A conflicting remote edit must survive and be visible; restore only fixture probes.
  a=Path(paths['auth'])/'src/conflict.ts';b=Path(folders['auth']['host_path'])/'src/conflict.ts'
  a.write_text('base');laptop.checkpoint(args,saved,False)
  b.write_text('remote fixture edit');a.write_text('laptop fixture edit')
  mutagen(binary,'sync','flush',ids['auth'],timeout=30)
  records=laptop.owned_sessions(args,saved)
  assert records['auth'].get('conflicts');assert b.read_text()=='remote fixture edit'
  b.write_text('laptop fixture edit');laptop.checkpoint(args,saved,False)
  result['remote_conflicts_preserved_and_extra_files_rejected']=True
  laptop.checkpoint(args,saved,True);assert checkpoints[-1]['paused'];assert laptop.clean(laptop.owned_sessions(args,saved),paused=True)
  result['freeze_checkpoint']=True
  result['passed']=True;save('profiles/sean/mutagen-fixture.json',result)
  print(json.dumps(result,indent=2))
 finally:
  for kind,name in [('pod',pod_name),('networkpolicy','chart-mutagen-fixture')]:
   obj=mongo.kget(kind,name);mongo.owned(obj)
   if obj:run(['kubectl','-n',mongo.NS,'delete',kind,name,'--wait=true'])
  if statefile.exists():
   saved=json.loads(statefile.read_text())
   if saved.get('sessions'):mutagen(binary,'sync','pause',*saved['sessions'].values())
  mutagen(binary,'daemon','stop')
  sshd.terminate();sshd.wait(timeout=10)
  # Fixture source/dependencies/PVs/PVCs/session state retained, no database touched.

if __name__=='__main__':main()
