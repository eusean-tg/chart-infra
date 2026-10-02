"""Incus source permissions; k3s synchronization policy remains independent."""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sync_common as common

POLICY = 'chart-mutagen-box-v1'
VERSION = common.VERSION
REPOS = common.REPOS
manifest = common.manifest
fingerprint = common.fingerprint


def configuration():
    config = common.configuration()
    config['permissions'] = {'defaultFileMode': '0644', 'defaultDirectoryMode': '0755'}
    return config


def config_text():
    return json.dumps({'sync': {'defaults': configuration()}}, indent=2) + '\n'


def policy_hash():
    return hashlib.sha256((POLICY + config_text()).encode()).hexdigest()
