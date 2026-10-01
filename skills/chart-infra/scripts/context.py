#!/usr/bin/env python3
"""Read-only chart-infra diagnostic snapshot; never import lifecycle modules."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess


def read_json(path):
    return json.loads(path.read_text())


def attempt(operation):
    try:
        return operation()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # Do not echo raw stderr, JSON content or credential-bearing error text.
        return {'error': type(error).__name__}


def command(args, env=None):
    return subprocess.run(args, capture_output=True, text=True, check=True,
                          timeout=20, env=env).stdout


def constants(repo):
    tree = ast.parse((repo / 'mongo.py').read_text())
    return {node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in ('HDD_UUID', 'IMAGE')}


def local_storage(base, inv, expected):
    data = base / 'mongo'
    member = data / 'default/member-0'
    mount = attempt(lambda: command(['findmnt', '-n', '-o', 'TARGET,UUID',
                                    '--target', '/mnt/hdd']).split())
    return {
        'inventory_profile_matches': inv.get('profile') == base.name,
        'mount_identity_matches': mount == ['/mnt/hdd', expected['HDD_UUID']],
        'mount_query': 'failed' if isinstance(mount, dict) else 'completed',
        'inventory_disk_matches': inv.get('hdd_uuid') == expected['HDD_UUID'],
        'inventory_image_matches': inv.get('image') == expected['IMAGE'],
        'paths_are_not_symlinks': all(p.resolve() == p for p in
                                      (base, data, base / 'identity', member)),
        'volume_marker_matches': (data / '.shared-dev-volume').read_text() == inv['volume_marker'],
        'member_marker_matches': (member / '.shared-dev-member').read_text() == inv['markers'][0],
        'initialized': bool(inv.get('initialized')),
        'initialized_files_present': not inv.get('initialized') or (member / 'WiredTiger').is_file(),
    }


def dependency_report(inv):
    result = {}
    for service, repo in [('auth', 'auth-service-backend'), ('tharamine', 'tharamine-user-service'),
                          ('orange', 'orange-v2-backend')]:
        def inspect():
            source = Path(inv['sources'][repo]['path'])
            dep = inv['deps'][service]
            digest = hashlib.sha256(dep['image'].encode())
            for name in ('package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', '.pnpmfile.cjs'):
                path = source / name
                if path.exists():
                    digest.update(name.encode() + b'\0' + path.read_bytes())
            return {'source_path': str(source), 'source_exists': source.is_dir(),
                    'source_is_synced': bool(inv['sources'][repo].get('sync')),
                    'dependency_inputs_match': digest.hexdigest() == dep['key'],
                    'dependency_directory_exists': Path(dep['modules']).is_dir()}
        result[service] = attempt(inspect)
    return result


def sync_report(inv, repo, fingerprints):
    result = {key: inv.get(key) for key in ('transport', 'workspace', 'owner', 'policy', 'checkpoint_at')}
    result['operator_is_registered_owner'] = inv.get('uid') == os.getuid()
    result['registered'] = bool(inv.get('client_id') and inv.get('sessions'))
    result['frozen_attested_by_laptop'] = bool(inv.get('frozen'))
    result['laptop_daemon_queried'] = False
    result['checkpoint'] = {}
    # sync_common contains pure source-policy helpers, without lifecycle imports.
    policy = runpy.run_path(str(repo / 'sync_common.py'))
    result['policy_matches_checkout'] = inv.get('policy') == policy['POLICY'] and inv.get('policy_hash') == policy['policy_hash']()
    for service, folder in inv['folders'].items():
        def inspect():
            path = Path(folder['host_path'])
            marker = path / '.chart-sync-root'
            checkpoint = inv.get('checkpoint', {}).get(service, {})
            report = {'path': str(path), 'root_identity_matches': path.resolve() == path
                      and path.stat().st_uid == inv['uid'] and not marker.is_symlink()
                      and marker.read_text() == folder['marker'],
                      'recorded_files': checkpoint.get('files'),
                      'recorded_head': checkpoint.get('head'), 'recorded_dirty': checkpoint.get('dirty'),
                      'fingerprint_matches': 'not_checked'}
            if fingerprints:
                report['fingerprint_matches'] = policy['fingerprint'](policy['manifest'](path)) == checkpoint.get('fingerprint')
            return report
        result['checkpoint'][service] = attempt(inspect)
    return result


def cluster_report(host, profile, mongo_inv, apps_inv, sync_inv):
    env = dict(os.environ)
    env.setdefault('KUBECONFIG', str(Path.home() / '.kube/config'))

    def get(kind, name=None, namespace=None):
        args = ['kubectl', '--request-timeout=10s', 'get', kind]
        if name:
            args.append(name)
        if namespace:
            args += ['-n', namespace]
        return json.loads(command(args + ['-o', 'json'], env))

    nodes = get('nodes')['items']
    system = get('namespace', 'kube-system')
    matches = (len(nodes) == 1 and nodes[0]['metadata']['name'] == host['node']
               and system['metadata']['uid'] == host['kube_system_uid'])
    result = {'registered_cluster_matches': matches}
    if not matches:
        return result
    namespace = 'chart-' + profile
    volumes = {}
    if 'pv_uid' in mongo_inv:
        volumes['pv/' + namespace] = mongo_inv['pv_uid']
        volumes['pvc/mongo-data'] = mongo_inv['pvc_uid']
    for inv in (apps_inv, sync_inv):
        for key, uid in inv.get('volume_uids', {}).items():
            if key in volumes and volumes[key] != uid:
                raise ValueError('Inventory volume disagreement')
            volumes[key] = uid
    result['retained_volumes'] = {}
    for key, uid in volumes.items():
        def inspect():
            kind, name = key.split('/', 1)
            if kind not in ('pv', 'pvc'):
                raise ValueError('Unexpected inventory kind')
            obj = get(kind, name, namespace if kind == 'pvc' else None)
            return {'uid_matches': obj['metadata']['uid'] == uid,
                    'owner_matches': obj['metadata'].get('labels', {}).get('app.kubernetes.io/managed-by') == namespace}
        result['retained_volumes'][key] = attempt(inspect)
    result['pods'] = attempt(lambda: [
        {'name': pod['metadata']['name'], 'phase': pod.get('status', {}).get('phase'),
         'ready': any(c['type'] == 'Ready' and c['status'] == 'True'
                      for c in pod.get('status', {}).get('conditions', [])),
         'restarts': sum(c.get('restartCount', 0) for c in pod.get('status', {}).get('containerStatuses', []))}
        for pod in get('pods', namespace=namespace)['items']])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--state', type=Path, default=Path(os.environ.get('CHART_INFRA_STATE',
                        str(Path.home() / '.local/state/chart-infra'))))
    parser.add_argument('--cluster', action='store_true', help='Query Kubernetes; no apply, exec or daemon operations')
    parser.add_argument('--fingerprints', action='store_true', help='Read included source files and compare checkpoint hashes')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', args.profile) or args.profile == 'mongo-pilot':
        parser.error('invalid or reserved profile')
    repo = args.repo.expanduser().resolve()
    if not all((repo / name).is_file() for name in ('chart', 'mongo.py', 'sync_common.py')):
        parser.error('--repo must identify a chart-infra checkout')
    state = args.state.expanduser()
    base = Path('/mnt/hdd/shared-dev/profiles') / args.profile
    host = attempt(lambda: read_json(state / 'host.json'))
    inv = attempt(lambda: read_json(base / 'identity/inventory.json'))
    apps = attempt(lambda: read_json(base / 'identity/apps/inventory.json'))
    sync = attempt(lambda: read_json(base / 'identity/source-sync.json'))
    npmrc = Path.home() / '.npmrc'
    result = {'profile': args.profile, 'repo': str(repo), 'state_path': str(state),
              'mode': 'read-only diagnostic; CLI guards remain authoritative',
              'prerequisites': {name: shutil.which(name) is not None for name in
                                ('python3', 'kubectl', 'docker', 'findmnt', 'tailscale', 'ssh', 'git')},
              'npmrc': {'exists': npmrc.is_file(), 'private_permissions': attempt(lambda:
                       npmrc.is_file() and not bool(npmrc.stat().st_mode & 0o077)),
                       'registry_authentication': 'not_tested'},
              'host_registration': 'present' if 'error' not in host else {'error': host['error']},
              'storage': {'error': inv['error']} if 'error' in inv else attempt(lambda: local_storage(base, inv, constants(repo))),
              'apps': {'error': apps['error']} if 'error' in apps else {'selected_workspace': apps.get('workspace'),
                       'api_port': apps.get('api_port'), 'sources': dependency_report(apps)},
              'sync': {'error': sync['error']} if 'error' in sync else attempt(lambda: sync_report(sync, repo, args.fingerprints)),
              'cluster': 'not_checked'}
    if args.cluster:
        result['cluster'] = attempt(lambda: cluster_report(host, args.profile, inv, apps, sync))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
