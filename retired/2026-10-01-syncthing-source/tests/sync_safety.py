#!/usr/bin/env python3
"""Offline safety tests: root conflicts, exclusions and additive interrupted pairing."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sync_common import IGNORES, REPOS, check_roots, fingerprint, manifest, unrelated
spec=importlib.util.spec_from_file_location('laptop_sync',Path(__file__).resolve().parents[1]/'laptop-sync.py')
laptop=importlib.util.module_from_spec(spec);spec.loader.exec_module(laptop)


class FakeAPI:
    def __init__(self):
        self.config={'folders':[{'id':'openscape','path':'/unrelated/plans','type':'sendreceive'}],
                     'devices':[{'deviceID':'SELF'},{'deviceID':'EXISTING'}],
                     'options':{'globalAnnounceEnabled':True,'listenAddresses':['default']},
                     'gui':{'apiKey':'not-a-real-key'},'defaults':{'untouched':True}}
        self.calls=[];self.fail_folder=False

    def call(self,path,method='GET',data=None,**query):
        self.calls.append((path,method))
        if path=='config' and method!='GET':raise AssertionError('Whole config write forbidden')
        if path=='config':return copy.deepcopy(self.config)
        if path=='system/status':return {'myID':'SELF'}
        if path.startswith('config/defaults/'):return {}
        if path=='db/ignores':return {'ignore':IGNORES}
        if path in ('config/devices','config/folders') and method=='POST':
            if path.endswith('folders') and self.fail_folder:
                self.fail_folder=False;raise RuntimeError('simulated interrupted setup')
            name=path.split('/')[-1];key='id' if name=='folders' else 'deviceID'
            self.config[name]=[o for o in self.config[name] if o[key]!=data[key]]+[copy.deepcopy(data)]
            return None
        raise AssertionError((path,method))


class Safety(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.paths={}
        for svc in REPOS:
            p=self.root/svc;(p/'src').mkdir(parents=True)
            (p/'package.json').write_text('{}');(p/'src/index.ts').write_text('source')
            self.paths[svc]=str(p)

    def test_overlap_with_existing_parent_is_rejected(self):
        with self.assertRaises(ValueError):check_roots(self.paths,[('existing-parent',self.root)])

    def test_nested_new_roots_are_rejected(self):
        with self.assertRaises(ValueError):check_roots({'a':self.paths['auth'],'b':self.paths['auth']},[])

    def test_private_files_never_enter_manifest(self):
        p=Path(self.paths['auth']);(p/'.env.local').write_text('FAKE_SECRET')
        (p/'.npmrc').write_text('FAKE_TOKEN');(p/'.env.sample').write_text('PLACEHOLDER')
        (p/'.EnV.LoCaL').write_text('FAKE_SECRET');(p/'private.PeM').write_text('FAKE_KEY')
        (p/'node_modules').mkdir();(p/'node_modules/native.node').write_text('wrong-platform')
        files=manifest(p)
        self.assertEqual(set(files),{'package.json','src/index.ts','.env.sample'})
        old=fingerprint(files);(p/'src/index.ts').write_text('changed');self.assertNotEqual(old,fingerprint(manifest(p)))
        (p/'src/index.ts').unlink();self.assertNotIn('src/index.ts',manifest(p))

    def test_included_symlink_is_rejected(self):
        (Path(self.paths['auth'])/'src/link').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):manifest(self.paths['auth'])

    def test_conflict_copy_is_rejected(self):
        (Path(self.paths['auth'])/'src/a.sync-conflict-123.ts').write_text('other')
        with self.assertRaises(ValueError):manifest(self.paths['auth'])

    def test_interrupted_pairing_reconciles_additively(self):
        api=FakeAPI();export={'profile':'sean','workspace':'laptop','device_id':'REMOTE','address':'tcp://100.66.127.115:22001',
            'folders':{s:{'id':'chart-sean-laptop-'+s,'repo':r} for s,r in REPOS.items()}}
        remote_orig=laptop.remote
        laptop.remote=lambda args,action,payload=None:copy.deepcopy(export)
        self.addCleanup(setattr,laptop,'remote',remote_orig)
        args=SimpleNamespace(host='sean@pc',remote_dir='/workspace/chart-infra',profile='sean',**self.paths)
        state=self.root/'state/pairing.json';before=copy.deepcopy(api.config)
        api.fail_folder=True
        with self.assertRaises(RuntimeError):laptop.pair(args,api,None,state)
        saved=json.loads(state.read_text());laptop.pair(args,api,saved,state)
        saved=json.loads(state.read_text());laptop.pair(args,api,saved,state)
        ids={f['id'] for f in export['folders'].values()}
        self.assertEqual(unrelated(before,ids,'REMOTE'),unrelated(api.config,ids,'REMOTE'))
        self.assertEqual(len(api.config['folders']),4)
        self.assertTrue(all(f['paused'] for f in api.config['folders'] if f['id'] in ids))

    def test_existing_ignore_file_is_not_overwritten(self):
        p=Path(self.paths['auth'])/'.stignore';p.write_text('my-existing-rule\n')
        api=FakeAPI();original=laptop.remote
        laptop.remote=lambda *a,**k:{'device_id':'REMOTE','folders':{s:{'id':s} for s in REPOS}}
        self.addCleanup(setattr,laptop,'remote',original)
        args=SimpleNamespace(host='sean@pc',remote_dir='/workspace/chart-infra',**self.paths)
        with self.assertRaises(SystemExit):laptop.plan(args,api,None)
        self.assertEqual(p.read_text(),'my-existing-rule\n')


if __name__=='__main__':unittest.main()
