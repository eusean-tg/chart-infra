"""Repository-aware source selection shared by local and SSH fingerprint checks."""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess

VERSION = '0.18.1'
SKIP = {'.git', 'node_modules', 'dist', 'build', 'coverage', '.cache', '.next', '.nuxt',
        '.turbo', '.pnpm-store', '__pycache__', '.venv', '.ds_store', '.stfolder', '.stversions'}
PRIVATE = ('.env*', '*.pem', '*.key', '.npmrc', '.netrc')


def hard(parts):
    return any(p.casefold() in SKIP for p in parts)


def private(part):
    name = part.casefold()
    if name.endswith(('.sample', '.example')): return False
    return any(fnmatch.fnmatchcase(name, p) for p in PRIVATE)


def exceptions(root):
    raw = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--cached', '--deduplicate', '-z'])
    names = [x.decode() for x in raw.split(b'\0') if x]
    return sorted(n for n in names if not hard(Path(n).parts) and any(private(p) for p in Path(n).parts))


def ignored(parts, tracked):
    return hard(parts) or ('/'.join(parts) not in tracked and any(private(p) for p in parts))


def manifest(root, tracked):
    root = Path(root)
    if not root.is_dir() or root.resolve() != root: raise RuntimeError('Missing/symlinked source root')
    files = {}; seen = set(); tracked = set(tracked)
    for here, dirs, names in os.walk(root, followlinks=False):
        rel = Path(here).relative_to(root)
        def include(name, directory=False):
            path = rel / name
            if (Path(here) / name).is_symlink() or hard(path.parts): return False
            if directory and any(t.startswith(path.as_posix() + '/') for t in tracked): return True
            return not ignored(path.parts, tracked)
        dirs[:] = sorted(d for d in dirs if include(d, True))
        for name in sorted(names):
            if not include(name): continue
            path = Path(here) / name; relative = (rel / name).as_posix()
            if not path.is_file(): raise RuntimeError('Unsupported source file: ' + relative)
            folded = relative.casefold()
            if folded in seen: raise RuntimeError('Case-colliding source file: ' + relative)
            seen.add(folded)
            with path.open('rb') as f: files[relative] = hashlib.file_digest(f, 'sha256').hexdigest()
    return files


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def insensitive(pattern):
    return ''.join('[' + c.lower() + c.upper() + ']' if c.isascii() and c.isalpha() else c for c in pattern)


def literal(path):
    # Escape glob metacharacters without changing case-sensitive tracked paths.
    return ''.join('\\' + c if c in '*?[]\\' else c for c in path)


def configuration(tracked):
    patterns = [insensitive(p) for p in PRIVATE]
    patterns += ['!' + insensitive('*.sample'), '!' + insensitive('*.example')]
    parents = set()
    for path in tracked:
        for parent in Path(path).parents:
            if str(parent) != '.' and any(private(p) for p in parent.parts): parents.add(parent.as_posix())
    for parent in sorted(parents, key=lambda x: (x.count('/'), x)):
        patterns += ['!/' + literal(parent), '/' + literal(parent) + '/*']
    patterns += ['!/' + literal(p) for p in tracked]
    patterns += [insensitive(p) for p in sorted(SKIP)]
    return {'mode': 'one-way-safe', 'hash': 'sha256', 'scanMode': 'full',
            'symlink': {'mode': 'ignore'}, 'ignore': {'vcs': True, 'paths': patterns}}
