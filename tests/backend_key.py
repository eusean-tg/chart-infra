#!/usr/bin/env python3
"""Retained backend key provisioning: additive config and no silent rotation."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apps


class BackendKey(unittest.TestCase):
    def provision(self, root):
        with contextlib.redirect_stdout(io.StringIO()):
            apps.prepare_backend_key(root)

    def test_additive_and_repeat_preserves_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'auth';root.mkdir()
            config={'ALGORITHM':'RS256','JWT_PRIVATE_KEY':'synthetic-existing-issuer'}
            (root/'config.json').write_text(json.dumps(config))
            (root/'private.pem').write_text('synthetic-existing-key')
            self.provision(root)
            after={p.name:p.read_bytes() for p in root.iterdir()}
            self.provision(root)
            self.assertEqual(after,{p.name:p.read_bytes() for p in root.iterdir()})
            self.assertEqual(json.loads(after['config.json']),{**config,'PRIVATE_KEY_PATH_BACKEND':'/run/chart/backend-private.pem'})
            self.assertEqual(after['private.pem'],b'synthetic-existing-key')
            self.assertEqual((root/'backend-private.pem').stat().st_mode & 0o077,0)
            backups=list(root.parent.glob('auth-config-before-backend-key-*.json'))
            self.assertEqual(len(backups),1)
            self.assertEqual(json.loads(backups[0].read_text()),config)
            (root/'backend-private.pem').unlink()  # Synthetic test directory only.
            with self.assertRaisesRegex(SystemExit,'refusing regeneration'):
                self.provision(root)
            self.assertFalse((root/'backend-private.pem').exists())

    def test_foreign_path_or_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config={'ALGORITHM':'RS256','PRIVATE_KEY_PATH_BACKEND':'/foreign/key.pem'}
            (root/'config.json').write_text(json.dumps(config))
            with self.assertRaisesRegex(SystemExit,'path differs'):
                self.provision(root)
            (root/'config.json').write_text(json.dumps({'ALGORITHM':'RS256'}))
            (root/'backend-private.pem').symlink_to(root/'missing')
            with self.assertRaisesRegex(SystemExit,'symlinked'):
                self.provision(root)


if __name__=='__main__':unittest.main()
