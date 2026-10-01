#!/usr/bin/env python3
"""Prove a source generation executes without changing Pod UID/image; restore edits."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import guard,load,save,lock
import apps,mongo

def main():
 guard();held=lock('chart-apps-sean');apps.setup('sean');inv=apps.record();results={}
 for svc,(repo,_,_) in apps.SERVICES.items():
  def pod():
   items=json.loads(subprocess.check_output(['kubectl','-n','chart-sean','get','pod','-l','chart-app='+svc,'-o','json']))['items']
   assert len(items)==1
   return items[0]
  before=pod();path=Path(inv['sources'][repo]['path'])/'src/index.ts';original=path.read_bytes()
  marker='chart-source-generation:'+svc+':'+uuid.uuid4().hex
  try:
   path.write_bytes(original+('\nconsole.log('+json.dumps(marker)+');\n').encode())
   deadline=time.monotonic()+90
   while time.monotonic()<deadline:
    logs=subprocess.check_output(['kubectl','-n','chart-sean','logs','deployment/'+svc,'--tail=150'],text=True)
    if marker in logs:break
    time.sleep(1)
   else:raise RuntimeError(svc+': changed source generation never executed')
   after=pod()
   assert before['metadata']['uid']==after['metadata']['uid']
   assert before['spec']['containers'][0]['image']==after['spec']['containers'][0]['image']
   results[svc]={'pod_uid_unchanged':True,'image_unchanged':True,'generation_seen':marker,'pod_uid':after['metadata']['uid']}
   print(svc+': source generation observed; Pod/image unchanged',flush=True)
  finally:path.write_bytes(original)
  # Wait for the original source to restart and become ready before moving on.
  time.sleep(3)
  subprocess.run(['kubectl','-n','chart-sean','wait','--for=condition=ready','pod/'+before['metadata']['name'],'--timeout=120s'],check=True)
 save('profiles/sean/watchers.json',results)

if __name__=='__main__':main()
