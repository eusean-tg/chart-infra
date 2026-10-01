#!/usr/bin/env python3
"""Run the reviewed synthetic role fixture, dry-run first, in script-migration."""
import argparse
import json
from pathlib import Path
import subprocess
import apps, mongo
from common import guard, load, lock, run, save

ID='20261001_001_shared_dev_roles'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');a=p.parse_args()
    guard();held=lock('chart-apps-sean');apps.setup('sean');inv=apps.record()
    deps=load('dependencies/sean/pilot/script-migration.json')
    objs=apps.storage('fixture-source',Path(deps['source']))+apps.storage('fixture-deps',Path(deps['modules']))
    mongo.apply_owned(objs)
    name='chart-fixture-live' if a.apply else 'chart-fixture-dry'
    old=mongo.kget('job',name);mongo.owned(old)
    if old:
        if old.get('status',{}).get('succeeded'): print('Fixture job already succeeded; not replayed.');return
        raise SystemExit('Prior fixture job did not succeed; inspect it before retrying')
    script='''const fs=require('fs'); const {spawnSync}=require('child_process');
const auth=JSON.parse(fs.readFileSync('/auth/config.json'));const user=JSON.parse(fs.readFileSync('/user/config.json'));
const env={...process.env,CHART_INFRA_FIXTURE:'sean-offline-v1',MONGO_URI_AUTH:auth.MONGO_URI,MONGO_DB_NAME_AUTH:auth.MONGO_DB_NAME,
MONGO_URI_USER_SERVICE:user.MONGO_URI,MONGO_DB_NAME_USER_SERVICE:user.MONGO_DB_NAME};
const r=spawnSync('node',['migrate.js','up','''+json.dumps(ID)+('' if a.apply else ",'--dry-run'")+'''],{env,stdio:'inherit'});process.exit(r.status??1);'''
    c={'name':'fixture','image':inv['image'],'workingDir':'/app','command':['node','-e',script],
       'securityContext':apps.containersecurity(),'volumeMounts':[{'name':'source','mountPath':'/app','readOnly':True},
        {'name':'deps','mountPath':'/app/node_modules','readOnly':True},{'name':'auth','mountPath':'/auth','readOnly':True},
        {'name':'user','mountPath':'/user','readOnly':True},{'name':'tmp','mountPath':'/tmp'}]}
    job=mongo.object_('Job',name,{'backoffLimit':0,'activeDeadlineSeconds':180,'template':{'metadata':{'labels':{'chart-runtime':'apps','chart-app':'fixture'}},
     'spec':{'restartPolicy':'Never','automountServiceAccountToken':False,'securityContext':apps.podsecurity(),'containers':[c],
      'volumes':[{'name':n,'persistentVolumeClaim':{'claimName':v}} for n,v in [('source','fixture-source'),('deps','fixture-deps'),('auth','auth-config'),('user','tharamine-config')]]+[{'name':'tmp','emptyDir':{}}]}}},api='batch/v1')
    if a.apply:
        prior=mongo.kget('job','chart-fixture-dry')
        if not prior or not prior.get('status',{}).get('succeeded'):raise SystemExit('Successful dry-run required before apply')
    mongo.apply_owned([job]);run(['kubectl','-n',mongo.NS,'wait','--for=condition=complete','job/'+name,'--timeout=180s'])
    output=run(['kubectl','-n',mongo.NS,'logs','job/'+name],capture=True)
    # Logs are private. Connection credentials must not enter shared output.
    from common import STATE
    for svc in ('auth','tharamine'):
        uri=json.loads((mongo.IDENTITY/'apps'/svc/'config.json').read_text())['MONGO_URI'];output=output.replace(uri,'[redacted URI]')
    (STATE/(name+'.log')).write_text(output)
    print(name+': completed. Private log saved.')

if __name__=='__main__':main()
