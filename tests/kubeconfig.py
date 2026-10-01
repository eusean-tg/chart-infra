#!/usr/bin/env python3
"""Regression checks for shell-independent kubeconfig and read-only sync commands."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import common
import sync


class Kubeconfig(unittest.TestCase):
    def child_environment(self,override=None):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ,HOME=tmp,CHART_INFRA_STATE=tmp+'/state')
            env.pop('KUBECONFIG',None)
            if override is not None:env['KUBECONFIG']=override
            child='import os; print(os.environ["KUBECONFIG"])'
            script='import common,os,json,subprocess,sys; print(json.dumps([os.environ["KUBECONFIG"], subprocess.check_output([sys.executable,"-c",'+repr(child)+']).decode().strip()]))'
            values=json.loads(subprocess.check_output([sys.executable,'-c',script],cwd=ROOT,env=env,text=True))
            return values,tmp

    def test_unset_uses_user_config_and_children_inherit(self):
        values,home=self.child_environment()
        self.assertEqual(values,[home+'/.kube/config']*2)

    def test_explicit_override_is_preserved(self):
        values,_=self.child_environment('/explicit/first:/explicit/second')
        self.assertEqual(values,['/explicit/first:/explicit/second']*2)

    def test_read_still_rejects_wrong_cluster(self):
        with patch.object(common,'load',return_value={'node':'expected','kube_system_uid':'expected'}),patch.object(common,'run',side_effect=[json.dumps({'items':[{'metadata':{'name':'other'}}]}),'other']):
            with self.assertRaises(SystemExit):common.guard(check_capacity=False)

    def test_reads_skip_lifecycle_locks_but_keep_guard(self):
        for action in ('export','status'):
            with self.subTest(action=action),patch.object(sys,'argv',['sync.py',action,'--profile','sean']),patch.object(sync,'guard') as guard,patch.object(sync,'lock',side_effect=AssertionError('Read took lifecycle lock')),patch.object(sync.mongo,'configure') as configure,patch.object(sync,'export',return_value={}),patch.object(sync,'inventory',return_value={'transport':'mutagen-ssh','workspace':'laptop','frozen':False}),patch('builtins.print'):
                sync.main()
                guard.assert_called_once_with(check_capacity=False)
                configure.assert_called_once_with('sean')

    def test_mutation_keeps_all_locks_and_capacity_guard(self):
        with patch.object(sys,'argv',['sync.py','prepare','--profile','sean']),patch.object(sync,'guard') as guard,patch.object(sync,'lock') as lock,patch.object(sync.mongo,'configure'),patch.object(sync,'prepare'):
            sync.main()
            guard.assert_called_once_with(check_capacity=True)
            self.assertEqual([c.args[0] for c in lock.call_args_list],['chart-apps-sean','chart-mongo-sean','chart-sync-sean'])


if __name__=='__main__':unittest.main()
