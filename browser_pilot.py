#!/usr/bin/env python3
"""Temporary, loopback-forwarded real frontend for offline browser acceptance."""
from pathlib import Path
import json
import apps,mongo
from common import guard,load,lock

def main():
    guard();held=lock('chart-browser-pilot');apps.setup('sean');inv=apps.record()
    dep=load('dependencies/sean/pilot/kiyotaka-frontend.json');source=Path(dep['source'])
    # Vite and its generated locale bundles need a writable isolated test checkout.
    # This is not a developer Syncthing mirror.
    import shutil
    shutil.copyfile(Path(__file__).resolve().parent/'runtime/vite.offline.config.ts',source/'qa/verification/shared-dev-offline.config.ts')
    objects=apps.storage('browser-source',source)+apps.storage('browser-deps',Path(dep['modules']))
    env={'HOME':'/tmp','HUSKY':'0','VITE_PORT':'8097','VITE_BACKEND_API':'/api/v1','VITE_BACKEND_DOMAIN':'http://orange:3000',
         'VITE_BACKEND_PROXY_TARGET':'http://orange:3000','VITE_BACKEND_PROXY_ORIGIN':'http://localhost:8080',
         'VITE_APP_ORIGIN':'http://localhost:8097','VITE_V2_WEB_SOCKET_DOMAIN':'ws://127.0.0.1:9',
         'VITE_POSTHOG_API_KEY':'','VITE_DEV_TOKEN':''}
    pod=mongo.object_('Pod','browser-vite',{'automountServiceAccountToken':False,'securityContext':apps.podsecurity(),
      'containers':[{'name':'vite','image':inv['image'],'workingDir':'/app','command':['pnpm','run','dev','--config','qa/verification/shared-dev-offline.config.ts','--host','0.0.0.0','--strictPort'],
       'env':[{'name':k,'value':v} for k,v in env.items()],'securityContext':apps.containersecurity(),
       'volumeMounts':[{'name':'source','mountPath':'/app'},{'name':'deps','mountPath':'/app/node_modules'},{'name':'tmp','mountPath':'/tmp'}],
       'readinessProbe':{'tcpSocket':{'port':8097},'periodSeconds':2}}],
      'volumes':[{'name':'source','persistentVolumeClaim':{'claimName':'browser-source'}},{'name':'deps','persistentVolumeClaim':{'claimName':'browser-deps'}},{'name':'tmp','emptyDir':{}}]})
    pod['metadata']['labels'].update({'chart-app':'browser-vite','chart-runtime':'apps'})
    mongo.apply_owned(objects+[pod])
    print('Frontend test Pod created. Use kubectl -n chart-sean port-forward pod/browser-vite 8097:8097 --address 127.0.0.1')

if __name__=='__main__':main()
