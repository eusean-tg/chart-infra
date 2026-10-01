#!/usr/bin/env python3
"""Offline data/auth/network separation checks across two prepared chart profiles."""
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mongo as m
from checks import external
m.guard()
profiles=['mongo-pilot-single','mongo-isolation-check']
held=[m.lock('chart-mongo-'+p) for p in sorted(profiles)]
info={}
for profile in profiles:
    m.configure(profile);inv=m.inventory();m.ready()
    report=external(m,'d.isolation.updateOne({_id:'+json.dumps(profile)+'},{$set:{profile:'+json.dumps(profile)+'}},{upsert:true,writeConcern:{w:"majority"}});print(JSON.stringify(d.isolation.find({}).toArray()));')
    assert len(report)==1 and report[0]['profile']==profile
    info[profile]={'uri':(m.OUT/'compass-uri.txt').read_text().strip(),'ip':m.kget('service','mongo-access')['spec']['clusterIP'],'volume':inv['pv_uid'],'claim':inv['pvc_uid']}
a,b=profiles
assert info[a]['volume']!=info[b]['volume'] and info[a]['claim']!=info[b]['claim']
m.configure(b)
wrong=info[a]['uri'].split('@')[0]+'@'+info[b]['uri'].split('@')[1]
try:
    external(m,'print(JSON.stringify({unexpected:true}));',uri=wrong)
except RuntimeError as e:
    assert 'Authentication failed' in str(e), 'Unexpected failure instead of auth denial'
else: raise AssertionError('Profile A credentials authenticated to B')
m.configure(a)
p=subprocess.run(['kubectl','-n',m.NS,'exec','mongo-0','--','bash','-c',f'timeout 3 bash -c "exec 3<>/dev/tcp/{info[b]["ip"]}/27017"'],capture_output=True,text=True,timeout=10)
assert p.returncode!=0, 'Cross-namespace TCP unexpectedly succeeded'
result={'passed':True,'profiles':profiles,'synthetic_data_isolated':True,'credentials_not_interchangeable':True,'different_retained_pv_pvc':True,'cross_namespace_tcp_denied':True,'developer_linux_account_rbac_not_part_of_this_test':True}
m.save('profile-isolation.json',result)
print(json.dumps(result,indent=2))
