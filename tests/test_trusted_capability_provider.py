from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.json_contracts import canonical_fingerprint
from scripts.trusted_capability_provider import TrustedCapabilityProvider
from scripts.trusted_cli import TrustedCliError, TrustedCliStore


class _Approve:
    def confirm_cli_enrollment(self, **kwargs):
        return None


def _cli(path: Path, schema: str | None = None) -> Path:
    payload = schema or json.dumps({
        "modes": ["text2video"],
        "models": [{"name": "seedance", "modes": ["text2video"], "resolutions": ["720p"], "ratios": ["16:9"], "duration_min_seconds": 4, "duration_max_seconds": 8}],
        "resolutions": {"video": ["720p"]}, "ratios": ["16:9"],
    })
    path.write_text(f'''#!/bin/sh
case "$1" in
  --version) echo '{{"version":"1.4.18","commit":"abcdef0"}}' ;;
  schema) cat <<'EOF'
{payload}
EOF
  ;;
esac
''')
    path.chmod(0o500)
    return path


class TrustedCapabilityProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cli = _cli(root / "dreamina")
        self.store = TrustedCliStore(root / "config" / "trusted-cli.json")
        self.store.enroll(self.cli, approval_provider=_Approve())

    def test_capture_binds_closed_inode_and_snapshot_receipt(self):
        evidence = TrustedCapabilityProvider(self.store).capture()
        self.assertEqual(set(evidence), {"snapshot", "identity_receipt"})
        receipt = evidence["identity_receipt"]
        self.assertEqual(set(receipt), {"cli_path", "cli_sha256", "device", "inode", "size_bytes", "mode", "owner_uid", "cli_version", "cli_commit", "captured_at", "snapshot_fingerprint"})
        self.assertEqual(receipt["snapshot_fingerprint"], canonical_fingerprint(evidence["snapshot"]))
        self.assertEqual(receipt["mode"], 0o500)

    def test_path_swap_during_adapter_capture_fails_closed_and_adapter_closes(self):
        from scripts.dreamina_adapter import DreaminaAdapter as RealAdapter
        closed = []

        class SwappingAdapter(RealAdapter):
            def capability_snapshot(inner_self):
                snapshot = super().capability_snapshot()
                replacement = self.cli.with_suffix(".replacement")
                _cli(replacement)
                os.replace(replacement, self.cli)
                return snapshot

            def close(inner_self):
                closed.append(True)
                super().close()

        with patch("scripts.trusted_capability_provider.DreaminaAdapter", SwappingAdapter):
            with self.assertRaisesRegex(TrustedCliError, "path changed"):
                TrustedCapabilityProvider(self.store).capture()
        self.assertEqual(closed, [True])

    def test_noncanonical_or_schema_invalid_snapshot_fails_closed(self):
        invalid = _cli(Path(self.tmp.name) / "invalid", '{"modes":["invented"]}')
        store = TrustedCliStore(Path(self.tmp.name) / "bad-config" / "trusted-cli.json")
        store.enroll(invalid, approval_provider=_Approve())
        with self.assertRaises(Exception):
            TrustedCapabilityProvider(store).capture()

    def test_digest_or_permission_drift_fails_closed(self):
        self.cli.chmod(0o700)
        self.cli.write_text("#!/bin/sh\necho changed\n")
        self.cli.chmod(0o500)
        with self.assertRaisesRegex(TrustedCliError, "digest"):
            TrustedCapabilityProvider(self.store).capture()
        self.cli.chmod(0o522)
        with self.assertRaisesRegex(TrustedCliError, "unsafe"):
            TrustedCapabilityProvider(self.store).capture()


if __name__ == "__main__":
    unittest.main()
