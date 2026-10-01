#!/usr/bin/env python3
"""Publish the reviewed Sean sync handoff, refusing concurrently edited vault notes."""
import hashlib
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VAULT=Path('/home/sean/obsidian/vault/Shared Dev')
expected=json.loads(Path('/tmp/chart-vault-before.json').read_text())
sources={
 'Chart Dev — Getting Started.md':ROOT/'docs/GETTING-STARTED.md',
 'Chart Dev — Agent Onboarding.md':ROOT/'docs/AGENT-ONBOARDING.md',
 **{name:ROOT/'docs/design'/name for name in expected if name.startswith('Per-profile')},
}
for name,digest in expected.items():
 p=VAULT/name
 actual=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
 if actual!=digest:raise SystemExit('Vault note changed since review; reconcile first: '+name)
for name,source in sources.items():
 text=source.read_text()
 if name=='Chart Dev — Getting Started.md':
  text=text.replace('[the onboarding runbook](AGENT-ONBOARDING.md)','[[Chart Dev — Agent Onboarding|the onboarding runbook]]')
 (VAULT/name).write_text(text)
print('Published agent onboarding and updated daily-use/design notes; original Fable review untouched.')
os.umask(0o077)
hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob('*')
        if p.is_file() and '__pycache__' not in p.parts}
(Path.home()/'.local/state/chart-infra/project-files.json').write_text(json.dumps(hashes,indent=2)+'\n')
