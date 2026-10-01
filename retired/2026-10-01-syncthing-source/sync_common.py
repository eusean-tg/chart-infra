"""Shared, standard-library-only Syncthing helpers for the PC and laptop."""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

REPOS = {'auth': 'auth-service-backend', 'tharamine': 'tharamine-user-service',
         'orange': 'orange-v2-backend'}
SKIP = {'.git', '.stfolder', '.stignore', '.stversions', 'node_modules', 'dist',
        'build', 'coverage', '.cache', '.next', '.nuxt', '.turbo', '.pnpm-store',
        '.DS_Store', '.npmrc', '.yarnrc.yml'}
PRIVATE = ('.env*', '*.key', '*.pem', '*.p12', '*.pfx')
TEMPLATES = {'.env.sample', '.env.example', '.env.template'}
LEGACY_IGNORES = ['// chart-infra source sync v1', '!/.env.sample', '!/.env.example',
           '!/.env.template'] + sorted(SKIP - {'.stignore', '.stfolder'}) + list(PRIVATE)
IGNORES = ['// chart-infra source sync v2', '(?i)!/.env.sample', '(?i)!/.env.example',
           '(?i)!/.env.template'] + ['(?i)'+s for s in sorted(SKIP - {'.stignore', '.stfolder'})] + ['(?i)'+s for s in PRIVATE]


def ignored(parts):
    for i, part in enumerate(parts):
        part = part.casefold()
        if part in SKIP or part.startswith('.syncthing.'):
            return True
        if not (i == 0 and part in TEMPLATES) and any(fnmatch.fnmatchcase(part, pat) for pat in PRIVATE):
            return True
    return False


def manifest(root):
    root = Path(root)
    files = {}
    for here, dirs, names in os.walk(root, followlinks=False):
        rel = Path(here).relative_to(root)
        for name in dirs + names:
            p = Path(here) / name
            if not ignored((rel / name).parts) and p.is_symlink():
                raise ValueError(f'Symlink in source mirror is unsupported: {rel / name}')
        dirs[:] = sorted(n for n in dirs if not ignored((rel / n).parts))
        for name in sorted(names):
            p = Path(here) / name
            key = rel / name
            if ignored(key.parts):
                continue
            if '.sync-conflict-' in name.casefold():
                raise ValueError(f'Unresolved sync conflict: {key}')
            if not p.is_file():
                raise ValueError(f'Unsupported source entry: {key}')
            files[key.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return files


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check_roots(paths, existing):
    """Reject nested/duplicate roots, including already registered parent folders."""
    seen = []
    for name, raw in paths.items():
        p = Path(raw).expanduser().resolve(strict=True)
        if not p.is_dir() or not (p / 'package.json').is_file() or not (p / 'src/index.ts').is_file():
            raise ValueError(f'{name}: select a backend checkout containing package.json and src/index.ts')
        for label, other in seen + existing:
            q = Path(other).expanduser().resolve()
            if p == q or p in q.parents or q in p.parents:
                raise ValueError(f'{name}: overlaps {label} ({q}); existing folders must be handled explicitly')
        seen.append((name, p))
    return {name: str(p) for name, p in seen}


class API:
    def __init__(self, url, key):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ('http', 'https') or u.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Syncthing administration must use a loopback URL')
        self.url, self.key = url.rstrip('/'), key

    def call(self, path, method='GET', data=None, **query):
        url = self.url + '/rest/' + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        body = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={'X-API-Key': self.key, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            # Do not leak configuration contents / API keys in exceptions.
            raise RuntimeError(f'Syncthing {method} {path}: HTTP {e.code}') from None
        return json.loads(raw) if raw else None


def ready(status):
    return (status.get('state') == 'idle' and not status.get('error') and
            all(status.get(k, 0) == 0 for k in ('needTotalItems', 'pullErrors', 'receiveOnlyTotalItems')))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', opener=lambda p, f: os.open(p, f, 0o600)) as out:
        json.dump(value, out, indent=2)
        out.write('\n')
    tmp.replace(path)


def unrelated(config, folders, device):
    """Comparable snapshot of configuration this pairing is forbidden to change."""
    out = dict(config)
    out['folders'] = [f for f in config['folders'] if f['id'] not in folders]
    out['devices'] = [d for d in config['devices'] if d['deviceID'] != device]
    return out
