#!/usr/bin/env python3
"""Exercise real non-interactive SSH without shell kubeconfig exports."""
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from common import STATE,guard,lock,save
spec=importlib.util.spec_from_file_location('fixture',ROOT/'tests/mutagen_runtime.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


def main():
    guard()
    directory,server=fixture.ssh_fixture()
    ssh=['ssh','-F',str(directory/'ssh_config'),'-o','BatchMode=yes','sean@chart-mutagen-fixture']
    def execute(args):
        return subprocess.check_output(ssh+[shlex.join(args)],text=True,timeout=30)
    try:
        # Before chart imports common.py, SSH has no interactive-shell export.
        execute(['python3','-c','import os; assert "KUBECONFIG" not in os.environ'])
        held=[lock('chart-'+kind+'-sean') for kind in ('apps','mongo','sync')]
        results={}
        try:
            for action in ('export','status'):
                for prefix in (['python3',str(ROOT/'sync.py')],[str(ROOT/'chart'),'sync']):
                    result=json.loads(execute(prefix+[action,'--profile','sean']))
                    assert result['transport']=='mutagen-ssh'
                    results[' '.join(prefix[1:]+[action])]='passed'
        finally:
            for fd in held:fd.close()
        log=STATE/'profiles/sean/noninteractive-mutagen-fixture.log'
        with log.open('w') as out:
            # No env prefix, login shell, PTY, dotfile sourcing or kubeconfig flag.
            subprocess.run(ssh+[shlex.join(['python3',str(ROOT/'tests/mutagen_runtime.py')])],
                           stdout=out,stderr=subprocess.STDOUT,check=True,timeout=600)
        result={'passed':True,'ssh_environment_initially_unset':True,'json_reads_while_all_lifecycle_locks_held':results,
                'full_mutagen_fixture_over_noninteractive_ssh':True,
                'scope':'PC loopback SSH to Sean, not a Mac-origin client check','fixture_log':str(log)}
        save('profiles/sean/noninteractive-ssh.json',result)
        print(json.dumps(result,indent=2))
    finally:
        server.terminate();server.wait(timeout=10)


if __name__=='__main__':main()
