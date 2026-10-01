#!/usr/bin/env python3
"""Create a second synthetic account through normal auth APIs; deny private layout reads."""
import http.cookiejar
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import urllib.error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import guard,STATE,save
import mongo

def main():
 guard();mongo.configure('sean')
 email='peer.shared-dev@example.test'
 jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
 def req(path,body=None,token=None):
  headers={'Content-Type':'application/json','Origin':'http://localhost:8080'}
  if token:headers['Authorization']='Bearer '+token
  request=urllib.request.Request('http://127.0.0.1:8097/api/v1/'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
  try:
   with client.open(request,timeout=20) as r:return r.status,json.load(r)
  except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
 peer_file=STATE/'browser/peer-token-private.json'
 if peer_file.exists():
  token=json.loads(peer_file.read_text())['token']
 else:
  status,_=req('auth-v2/login-code/request',{'email':email});assert status==200,status
  logs=subprocess.check_output(['kubectl','-n','chart-sean','logs','deployment/auth','--since=2m'],text=True)
  code=re.findall(r'sign-in code for '+re.escape(email)+r': (\d{6})',logs)[-1]
  status,body=req('auth-v2/login-code/verify',{'email':email,'code':code});assert status==200,status
  if body.get('registrationRequired'):
   status,body=req('auth-v2/register/complete',{'registrationToken':body['registrationToken'],'username':'chartdevpeer'});assert status in (200,201),status
  token=body.get('token') or body.get('accessToken')
  if not token:raise RuntimeError('Token response shape differs; inspect keys: '+','.join(body))
 peer_file=STATE/'browser/peer-token-private.json'
 peer_file.write_text(json.dumps({'token':token}));peer_file.chmod(0o600)
 first=json.loads((STATE/'browser/session-private.json').read_text())
 owner_token=next(c['value'] for c in first['cookies'] if c['name']=='accessToken')
 existing=json.loads(mongo.shell('print(JSON.stringify(db.getSiblingDB("orange").layouts.findOne({name:"Shared dev private fixture"},{_id:1,layout_id:1})))'))
 if not existing:
  status,created=req('layouts',{'name':'Shared dev private fixture','description':'Synthetic private-layout access check','snapshot':{'fixture':'offline-v1'},'scriptRefs':[],'access':'private'},owner_token)
  assert status in (200,201),(status,created.get('message'))
  existing=json.loads(mongo.shell('print(JSON.stringify(db.getSiblingDB("orange").layouts.findOne({name:"Shared dev private fixture"},{_id:1,layout_id:1})))'))
 assert existing
 layout_id=existing.get('layout_id') or existing['_id']
 owner_status,_=req('layouts/'+layout_id,token=owner_token)
 peer_status,_=req('layouts/'+layout_id,token=token)
 assert owner_status==200,owner_status
 assert peer_status==404,peer_status
 workspace=json.loads(mongo.shell('print(JSON.stringify(db.getSiblingDB("orange").workspaces.findOne({name:"Shared dev offline fixture"},{_id:1,shortId:1})))'))
 # Readable workspace links are the product contract. Mutation is owner-gated.
 request=urllib.request.Request('http://127.0.0.1:8097/api/v1/workspaces/'+workspace['shortId'],data=json.dumps({'name':'unauthorized change'}).encode(),method='PUT',headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
 try:
  with client.open(request,timeout=20) as r:write_status=r.status
 except urllib.error.HTTPError as e:write_status=e.code
 assert write_status in (403,404),write_status
 save('profiles/sean/user-isolation.json',{'second_user_normal_login':True,'private_layout_owner_read':owner_status,'private_layout_peer_read':peer_status,'workspace_peer_write':write_status,'workspace_link_reads':'public by application design'})
 print('Private layout: owner 200, peer 404. Peer workspace edit denied.')

if __name__=='__main__':main()
