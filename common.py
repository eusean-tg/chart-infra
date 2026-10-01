"""Standalone chart-infra helpers; no imports or runtime dependency on meta_repo."""
import fcntl
import json
import os
from pathlib import Path
import subprocess

os.umask(0o077)
# k3s' kubectl otherwise defaults to its root-only server config. SSH commands
# do not load interactive shell exports. Set the process default once so direct
# subprocess calls (including port-forwards) inherit it too; preserve overrides.
os.environ.setdefault('KUBECONFIG', str(Path.home() / '.kube/config'))
STATE = Path(os.environ.get('CHART_INFRA_STATE', str(Path.home() / '.local/state/chart-infra')))
STATE.mkdir(parents=True, exist_ok=True, mode=0o700)


def run(args, data=None, capture=False):
    result = subprocess.run(args, input=data, text=True, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def load(name):
    return json.loads((STATE / name).read_text())


def save(name, value):
    path = STATE / name
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)
    return path


def lock(name):
    # Keep the original lock location shared with old compatibility commands.
    directory = Path.home() / '.cache/shared-dev'
    directory.mkdir(parents=True, exist_ok=True)
    fd = (directory / (name + '.lock')).open('a')
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def guard(check_capacity=True):
    expected = load('host.json')
    nodes = json.loads(run(['kubectl', 'get', 'nodes', '-o', 'json'], capture=True))['items']
    uid = run(['kubectl', 'get', 'ns', 'kube-system', '-o', 'jsonpath={.metadata.uid}'], capture=True)
    if len(nodes) != 1 or nodes[0]['metadata']['name'] != expected['node'] or uid != expected['kube_system_uid']:
        raise SystemExit('Refusing: kubectl does not target the registered single-node development cluster')
    if check_capacity:
        fs = os.statvfs('/')
        if fs.f_bavail / fs.f_blocks < 0.25:
            raise SystemExit('SSD free reserve below 25%; investigate first')


def apply(objects, dry=False):
    def inspect(value):
        if isinstance(value, dict):
            for key in ('containers','initContainers'):
                for container in value.get(key, []):
                    resources = container.get('resources', {})
                    if resources.get('requests') or resources.get('limits'):
                        raise SystemExit('Chart workloads must not declare CPU/memory requests or limits')
            for child in value.values(): inspect(child)
        elif isinstance(value, list):
            for child in value: inspect(child)
    inspect(objects)
    args = ['kubectl', 'apply', '-f', '-'] + (['--dry-run=server'] if dry else [])
    run(args, json.dumps({'apiVersion':'v1','kind':'List','items':objects}))
