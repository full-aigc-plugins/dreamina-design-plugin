from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts.native_approval import ApprovalDeniedError, NativeApprovalProvider


class NativeApprovalProviderTests(unittest.TestCase):
    def test_video_batch_confirmation_uses_specific_whole_batch_warning(self) -> None:
        provider = NativeApprovalProvider()
        request = {"action": "activate-video-batch-allowance", "total_credit_ceiling": 14}
        with patch.object(provider, "_confirm_dialog") as confirm:
            token = provider.confirm_video_batch(request)
        self.assertEqual(token, "native-video-batch-confirmed")
        message, button = confirm.call_args.args
        self.assertEqual(button, "批准整批")
        self.assertEqual(json.loads(message.split("\n\n", 1)[1]), request)
        self.assertIn("不可扩展", message)

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

    def test_audio_key_bootstrap_confirmation_binds_identity_path_action_and_impact(self) -> None:
        provider = NativeApprovalProvider()
        with patch.object(provider, "_confirm_dialog") as confirm:
            token = provider.confirm_audio_receipt_key_initialization(
                key_store_path="/private/keys/audio.key", action="rebootstrap",
                new_key_id="a" * 64, purpose="recover receipt signing",
                impact="previous signed receipts become unverifiable",
            )
        self.assertEqual(token, "native-audio-receipt-key-confirmed")
        message, button = confirm.call_args.args
        self.assertEqual(button, "确认重新生成密钥")
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertEqual(payload["action"], "rebootstrap")
        self.assertEqual(payload["new_key_id"], "a" * 64)
        self.assertEqual(payload["key_store_path"], "/private/keys/audio.key")
        self.assertIn("unverifiable", payload["impact"])


    def test_video_export_confirmation_binds_destination_checksum_and_rights(self) -> None:
        provider = NativeApprovalProvider()
        request = {
            "action": "export-video-project",
            "project_id": "vp_" + "1" * 24,
            "composition_version": "v001",
            "destination": "/approved/out/final.mp4",
            "sha256": "c" * 64,
            "rights_basis": "original_redesign",
        }
        with patch.object(provider, "_confirm_dialog") as confirm:
            token = provider.confirm_video_export(request)
        self.assertEqual(token, "native-video-export-confirmed")
        message, button = confirm.call_args.args
        self.assertEqual(button, "确认导出")
        self.assertEqual(json.loads(message.split("\n\n", 1)[1]), request)

    def test_video_export_confirmation_rejects_a_missing_binding(self) -> None:
        provider = NativeApprovalProvider()
        for missing in ("destination", "sha256", "composition_version", "rights_basis"):
            request = {
                "action": "export-video-project",
                "destination": "/approved/out/final.mp4",
                "sha256": "c" * 64,
                "composition_version": "v001",
                "rights_basis": "original_redesign",
            }
            request.pop(missing)
            with self.subTest(missing=missing):
                with patch.object(provider, "_confirm_dialog"):
                    with self.assertRaises(ApprovalDeniedError) as raised:
                        provider.confirm_video_export(request)
                self.assertIn(missing, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
