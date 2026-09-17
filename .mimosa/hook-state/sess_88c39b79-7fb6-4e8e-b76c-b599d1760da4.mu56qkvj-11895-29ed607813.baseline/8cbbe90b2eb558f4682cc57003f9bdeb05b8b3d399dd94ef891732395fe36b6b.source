from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from scripts.trusted_cli import TrustedCliError, TrustedCliStore


class _Approve:
    def confirm_cli_enrollment(self, **kwargs):
        self.kwargs = kwargs
        return "approved"


class TrustedCliStoreTests(unittest.TestCase):
    def test_enrollment_requires_native_approval_and_writes_private_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / "dreamina"
            cli.write_bytes(b"trusted-binary")
            cli.chmod(0o755)
            provider = _Approve()
            store = TrustedCliStore(path=root / "config" / "trusted-cli.json")
            result = store.enroll(cli, approval_provider=provider)
            self.assertEqual(result["cli_sha256"], hashlib.sha256(b"trusted-binary").hexdigest())
            self.assertEqual(provider.kwargs["path"], str(cli.resolve()))
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(store.path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(store.load(), result)

    def test_load_rejects_missing_or_overpermissive_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private" / "trusted-cli.json"
            store = TrustedCliStore(path=path)
            with self.assertRaises(TrustedCliError):
                store.load()
            path.parent.mkdir(mode=0o700)
            path.write_text('{"cli_path":"/x","cli_sha256":"' + "a" * 64 + '"}')
            os.chmod(path, 0o644)
            with self.assertRaises(TrustedCliError):
                store.load()


if __name__ == "__main__":
    unittest.main()
