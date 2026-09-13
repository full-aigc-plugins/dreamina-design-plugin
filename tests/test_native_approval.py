from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts.native_approval import NativeApprovalProvider


class NativeApprovalProviderTests(unittest.TestCase):
    def test_media_tool_enrollment_binds_kind_path_owner_and_digest(self) -> None:
        provider = NativeApprovalProvider()
        with patch.object(provider, "_confirm_dialog") as confirm:
            receipt = provider.confirm_media_tool_enrollment(
                kind="ffprobe", path="/trusted/ffprobe", owner_uid=501, sha256="a" * 64
            )
        self.assertEqual(receipt, "native-media-tool-trust-confirmed")
        message, button = confirm.call_args.args
        self.assertEqual(button, "信任此文件")
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertEqual(
            payload,
            {
                "action": "trust-media-tool",
                "kind": "ffprobe",
                "owner_uid": 501,
                "path": "/trusted/ffprobe",
                "sha256": "a" * 64,
            },
        )


if __name__ == "__main__":
    unittest.main()
