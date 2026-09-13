from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts.native_approval import ApprovalDeniedError, NativeApprovalProvider


class NativeApprovalProviderTests(unittest.TestCase):
    def test_video_rights_confirmation_displays_exact_binding_and_disclaimer(self) -> None:
        provider = NativeApprovalProvider()
        request = {
            "action": "assert-video-replication-rights",
            "project_id": "vp_" + "1" * 24,
            "source_sha256": "a" * 64,
            "creative_mode": "authorized_replication",
            "design_fingerprint": "b" * 64,
            "declarant": "user@example.test",
            "disclaimer": "not ownership verification or legal advice",
        }
        with patch.object(provider, "_confirm_dialog") as confirm:
            token = provider.confirm_video_rights(request)
        self.assertEqual(token, "native-video-rights-confirmed")
        message, button = confirm.call_args.args
        self.assertEqual(button, "确认声明")
        self.assertEqual(json.loads(message.split("\n\n", 1)[1]), request)
        self.assertIn("不验证所有权", message)
        self.assertIn("不构成法律建议", message)

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

    def test_unavailable_native_dialog_error_is_action_neutral(self) -> None:
        with patch("scripts.native_approval.platform.system", return_value="Linux"):
            with self.assertRaises(ApprovalDeniedError) as raised:
                NativeApprovalProvider._confirm_dialog("trust local tool", "approve")
        message = str(raised.exception)
        self.assertNotIn("paid", message)
        self.assertNotIn("submission", message)
        self.assertIn("guarded action", message)


if __name__ == "__main__":
    unittest.main()
