import unittest

from scripts.output_redactor import redact_text, redact_value


class OutputRedactorTests(unittest.TestCase):
    def test_recursive_redaction_removes_sensitive_fields(self) -> None:
        value = {"user_id": "u1", "access_token": "secret", "nested": {"device_code": "d1"}}
        self.assertEqual(redact_value(value), {"user_id": "u1", "access_token": "[REDACTED]", "nested": {"device_code": "[REDACTED]"}})

    def test_text_redaction_happens_before_byte_limit(self) -> None:
        result = redact_text("Authorization: Bearer secret-value\nvisible", max_bytes=40)
        self.assertNotIn("secret-value", result)
        self.assertLessEqual(len(result.encode("utf-8")), 40)
