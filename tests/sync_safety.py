#!/usr/bin/env python3
"""Offline source policy and checkpoint safety tests."""
import importlib.util
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sync_common import REPOS,POLICY,check_roots,fingerprint,manifest,policy_hash
import sync
spec=importlib.util.spec_from_file_location('laptop',Path(__file__).resolve().parents[1]/'laptop-sync.py')
laptop=importlib.util.module_from_spec(spec);spec.loader.exec_module(laptop)

class Safety(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.paths={}
  for svc in REPOS:
   p=self.root/svc;(p/'src').mkdir(parents=True)
   for name,value in [('package.json','{}'),('pnpm-lock.yaml','lock'),('src/index.ts','source'),('AGENTS.md','instructions')]:
    (p/name).write_text(value)
   self.paths[svc]=str(p)
 def test_overlap(self):
  with self.assertRaises(ValueError):check_roots(self.paths,[('existing',str(self.root))])
  with self.assertRaises(ValueError):check_roots({'a':self.paths['auth'],'b':self.paths['auth']},[])
 def test_secrets_and_builds(self):
  p=Path(self.paths['auth'])
  for n in ['.EnV.LoCaL','.NETRC','private.PeM','.DS_Store','.npmrc']: (p/n).write_text('FAKE_ONLY')
  (p/'node_modules').mkdir();(p/'node_modules/test').write_text('wrong platform')
  (p/'.env.sample').write_text('template');(p/'src/.env.sample').write_text('private nested')
  self.assertEqual(set(manifest(p)),{'package.json','pnpm-lock.yaml','src/index.ts','AGENTS.md','.env.sample'})
 def test_known_alias_only(self):
  p=Path(self.paths['auth']);(p/'CLAUDE.md').symlink_to('AGENTS.md');self.assertNotIn('CLAUDE.md',manifest(p))
  (p/'src/link').symlink_to('/etc/passwd')
  with self.assertRaises(ValueError):manifest(p)
 def test_graphify_and_husky_are_excluded_at_any_depth(self):
  p=Path(self.paths['auth']);before=fingerprint(manifest(p))
  for parent in (p,p/'src'):
   for name in ('graphify-out','.HuSkY'):
    (parent/name).mkdir();(parent/name/'generated.json').write_text('local tool output')
   (parent/'.GraphifyIgnore').write_text('local ignores')
  self.assertEqual(before,fingerprint(manifest(p)))
 def test_unused_policy_upgrade_preserves_identity(self):
  inv={'policy':'chart-mutagen-v1','policy_hash':sync.LEGACY_POLICIES['chart-mutagen-v1'],
       'registration':'retained','volume_uids':{'pvc/source':'retained-uid'},
       'folders':{},'frozen':False}
  original=copy.deepcopy(inv)
  with patch.object(sync,'inventory',return_value=inv),patch.object(sync,'save') as save,patch.object(sync.mongo,'write_json') as write:
   result=sync.upgrade_policy()
   self.assertTrue(result['changed']);save.assert_called_once()
   updated=write.call_args.args[1]
   self.assertEqual(updated,{**original,'policy':POLICY,'policy_hash':policy_hash()})
   self.assertEqual(inv,original)
 def test_registered_policy_cannot_be_upgraded_implicitly(self):
  inv={'policy':'chart-mutagen-v1','policy_hash':sync.LEGACY_POLICIES['chart-mutagen-v1'],'client_id':'laptop','folders':{}}
  with patch.object(sync,'inventory',return_value=inv),patch.object(sync.mongo,'write_json') as write:
   with self.assertRaises(SystemExit):sync.upgrade_policy()
   write.assert_not_called()
 def test_current_policy_upgrade_is_noop(self):
  with patch.object(sync,'inventory',return_value={'policy':POLICY,'policy_hash':policy_hash()}),patch.object(sync.mongo,'write_json') as write:
   self.assertFalse(sync.upgrade_policy()['changed']);write.assert_not_called()
 def test_bad_known_alias(self):
  p=Path(self.paths['auth']);(p/'CLAUDE.md').symlink_to('/etc/passwd')
  with self.assertRaises(ValueError):manifest(p)
 def test_conflicts_and_case_collisions(self):
  p=Path(self.paths['auth']);(p/'src/a.sync-conflict-123.ts').write_text('other')
  with self.assertRaises(ValueError):manifest(p)
  (p/'src/a.sync-conflict-123.ts').unlink();(p/'src/INDEX.ts').write_text('other')
  with self.assertRaises(ValueError):manifest(p)
 def test_edit_delete(self):
  p=Path(self.paths['auth']);a=fingerprint(manifest(p));(p/'src/index.ts').write_text('changed')
  self.assertNotEqual(a,fingerprint(manifest(p)));(p/'src/index.ts').unlink();self.assertNotIn('src/index.ts',manifest(p))
 def test_extra_remote_file_blocks_checkpoint(self):
  inv={'registration':'reg','client_id':'client','sessions':{s:'id-'+s for s in REPOS},'folders':{
   s:{'host_path':p,'repo':REPOS[s],'claim':'source-'+s} for s,p in self.paths.items()},'workspace':'test'}
  payload={'registration':'reg','policy_hash':policy_hash(),'client_id':'client','sessions':inv['sessions'],'paused':True,
   'repos':{s:{'fingerprint':fingerprint(manifest(p))} for s,p in self.paths.items()}}
  (Path(self.paths['auth'])/'extra.ts').write_text('PC-local file')
  with patch.object(sync,'inventory',return_value=inv),patch.object(sync,'save') as save:
   with self.assertRaises(SystemExit):sync.checkpoint(payload)
   save.assert_not_called()
 def test_wrong_owner_and_unpaused_selection(self):
  inv={'registration':'reg','client_id':'client','sessions':{'auth':'id'}}
  with self.assertRaises(SystemExit):sync.validate_payload(inv,{'registration':'wrong','policy_hash':policy_hash()})
  with patch.object(sync,'inventory',return_value={'workspace':'test','frozen':False}):
   with self.assertRaises(SystemExit):sync.assert_selection('test',{})
 def test_matching_checkpoint_persists_sources_and_freeze(self):
  inv={'registration':'reg','client_id':'client','sessions':{s:'id-'+s for s in REPOS},'folders':{
   s:{'host_path':p,'repo':REPOS[s],'claim':'source-'+s} for s,p in self.paths.items()},'workspace':'test'}
  payload={'registration':'reg','policy_hash':policy_hash(),'client_id':'client','sessions':inv['sessions'],'paused':True,
   'repos':{s:{'fingerprint':fingerprint(manifest(p)),'head':'fixture','dirty':False} for s,p in self.paths.items()}}
  with patch.object(sync,'inventory',return_value=inv),patch.object(sync,'save') as save,patch.object(sync.mongo,'write_json') as write:
   result=sync.checkpoint(payload)
   self.assertEqual(set(result),set(REPOS));self.assertTrue(inv['frozen']);write.assert_called_once()
   sources=save.call_args_list[0].args[1]
   sync.assert_selection('test',sources)
   (Path(self.paths['auth'])/'src/index.ts').write_text('later edit')
   with self.assertRaises(SystemExit):sync.assert_selection('test',sources)
 def test_connection_and_conflict_checks(self):
  r={'paused':False,'status':'watching','alpha':{'connected':True},'beta':{'connected':True}}
  self.assertTrue(laptop.clean({'auth':r}));r['conflicts']=[{}];self.assertFalse(laptop.clean({'auth':r}))
  del r['conflicts'];r['beta']['scanProblems']=[{}];self.assertFalse(laptop.clean({'auth':r}))

if __name__=='__main__':unittest.main()
