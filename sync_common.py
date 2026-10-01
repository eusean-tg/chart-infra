"""Shared source policy and Mutagen helpers. No credentials or local path conventions."""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

REPOS = {'auth':'auth-service-backend', 'tharamine':'tharamine-user-service', 'orange':'orange-v2-backend'}
VERSION = '0.18.1'
POLICY = 'chart-mutagen-v2'
SKIP = {'.git', '.stfolder', '.stignore', '.stversions', '.chart-sync-root', 'node_modules',
        'dist', 'build', 'coverage', '.cache', '.next', '.nuxt', '.turbo', '.pnpm-store',
        '.ds_store', '.npmrc', '.netrc', '.yarnrc.yml',
        'graphify-out', '.graphifyignore', '.husky'}
PRIVATE = ('.env*', '*.key', '*.pem', '*.p12', '*.pfx')
TEMPLATES = {'.env.sample', '.env.example', '.env.template'}


def insensitive(pattern):
    return ''.join('['+c.lower()+c.upper()+']' if c.isascii() and c.isalpha() else c for c in pattern)


def ignore_patterns():
    # Mutagen: last matching pattern wins. Match our case-insensitive fingerprint policy.
    return ([insensitive(x) for x in sorted(SKIP)] + [insensitive('.syncthing.*')] +
            [insensitive(x) for x in PRIVATE] + ['!/'+insensitive(x) for x in sorted(TEMPLATES)] +
            ['/'+insensitive('CLAUDE.md')])


def configuration():
    return {'mode':'one-way-safe', 'hash':'sha256', 'symlink':{'mode':'ignore'},
            'scanMode':'full', 'ignore':{'vcs':True, 'paths':ignore_patterns()},
            'permissions':{'defaultFileMode':'0600','defaultDirectoryMode':'0700'}}


def config_text():
    # JSON is a YAML subset, accepted by Mutagen's configuration parser.
    return json.dumps({'sync':{'defaults':configuration()}},indent=2)+'\n'


def policy_hash():
    return hashlib.sha256((POLICY+config_text()).encode()).hexdigest()


def ignored(parts):
    for i, raw in enumerate(parts):
        part=raw.casefold()
        if part in SKIP or part.startswith('.syncthing.'):return True
        if i==0 and len(parts)==1 and part=='claude.md':return True
        if not (i==0 and part in TEMPLATES) and any(fnmatch.fnmatchcase(part,x) for x in PRIVATE):return True
    return False


def manifest(root):
    root=Path(root)
    if not root.is_dir() or root.resolve()!=root:raise ValueError('Missing or symlinked source root')
    alias=root/'CLAUDE.md'
    if alias.is_symlink() and (os.readlink(alias)!='AGENTS.md' or not (root/'AGENTS.md').is_file() or (root/'AGENTS.md').is_symlink()):
        raise ValueError('Only the known root CLAUDE.md -> AGENTS.md alias is excluded; review this link')
    files={};seen=set()
    for here,dirs,names in os.walk(root,followlinks=False):
        rel=Path(here).relative_to(root)
        for name in sorted(dirs+names):
            p=Path(here)/name;key=rel/name
            if ignored(key.parts):continue
            if p.is_symlink():raise ValueError('Unexpected included symlink: '+str(key))
            fold=key.as_posix().casefold()
            if fold in seen:raise ValueError('Case-colliding source entries: '+str(key))
            seen.add(fold)
            if '.sync-conflict-' in name.casefold():raise ValueError('Unresolved source conflict: '+str(key))
        dirs[:]=sorted(n for n in dirs if not ignored((rel/n).parts))
        for name in sorted(names):
            p=Path(here)/name;key=rel/name
            if ignored(key.parts):continue
            if not p.is_file():raise ValueError('Unsupported source entry: '+str(key))
            files[key.as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return files


def fingerprint(files):
    return hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def check_roots(paths,existing):
    seen=[]
    for name,raw in paths.items():
        p=Path(raw).expanduser().resolve(strict=True)
        if not p.is_dir() or not (p/'package.json').is_file() or not (p/'src/index.ts').is_file():
            raise ValueError(name+': select a backend checkout containing package.json and src/index.ts')
        for label,other in seen+existing:
            q=Path(other).expanduser().resolve()
            if p==q or p in q.parents or q in p.parents:raise ValueError(name+': overlaps '+label+' ('+str(q)+')')
        seen.append((name,p))
    return {name:str(p) for name,p in seen}


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=path.with_suffix('.tmp')
    with open(tmp,'w',opener=lambda p,f:os.open(p,f,0o600)) as out:
        json.dump(value,out,indent=2);out.write('\n')
    tmp.replace(path)


def mutagen(binary,*args,timeout=180):
    return subprocess.check_output([binary,*args],text=True,timeout=timeout)


def sessions(binary):
    return json.loads(mutagen(binary,'sync','list','--template','{{json .}}')) or []
