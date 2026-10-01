#!/usr/bin/env python3
"""Stock source acceptance and preservation of retained pilot identities."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apps


class StockSource(unittest.TestCase):
    def test_stock_source_accepted_but_changed_lockfile_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inv = {'sources':{}, 'deps':{}}
            for svc, (repo, _, _) in apps.SERVICES.items():
                path = root/repo
                path.mkdir()
                (path/'package.json').write_text('{}')
                (path/'pnpm-lock.yaml').write_text('lockfileVersion: 9\n')
                h = hashlib.sha256(b'fixture-image')
                for name in ('package.json','pnpm-lock.yaml'):
                    h.update(name.encode()+b'\0'+(path/name).read_bytes())
                inv['sources'][repo] = {'path':str(path)}
                inv['deps'][svc] = {'image':'fixture-image','key':h.hexdigest()}
            apps.validate_sources(inv)  # No patched source or flag exists.
            (path/'pnpm-lock.yaml').write_text('changed')
            with self.assertRaisesRegex(SystemExit, 'dependency inputs changed'):
                apps.validate_sources(inv)

    def test_stock_config_retains_keys_and_pilot_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(apps.mongo, 'IDENTITY', Path(tmp)):
            root = Path(tmp)/'apps/tharamine'
            root.mkdir(parents=True)
            config = {'LOCAL_BACKGROUND_WORKERS_DISABLED':'true',
                      'MONGO_WAIT_QUEUE_TIMEOUT_MS':'120000',
                      'INTERNAL_SERVICE_KEY':'synthetic-fixture-only',
                      'OM_ROOM_NOTIFY_ENABLED':'false'}
            (root/'config.json').write_text(json.dumps(config))
            (root/'private.pem').write_text('synthetic fixture, not a key')
            (root/'launch.cjs').write_text('// fixture launcher')
            before = {p.name:p.read_bytes() for p in root.iterdir()}
            target = apps.stock_tharamine_config()
            self.assertEqual(before, {p.name:p.read_bytes() for p in root.iterdir()})
            self.assertEqual((target/'private.pem').read_bytes(), before['private.pem'])
            self.assertEqual(json.loads((target/'config.json').read_text()), {
                k:v for k,v in config.items() if k not in ('LOCAL_BACKGROUND_WORKERS_DISABLED','MONGO_WAIT_QUEUE_TIMEOUT_MS')})
            self.assertEqual(apps.stock_tharamine_config(), target)
            self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in target.iterdir()))
            (target/'launch.cjs').write_text('changed')
            with self.assertRaisesRegex(SystemExit, 'configuration differs'):
                apps.stock_tharamine_config()


if __name__ == '__main__':
    unittest.main()
