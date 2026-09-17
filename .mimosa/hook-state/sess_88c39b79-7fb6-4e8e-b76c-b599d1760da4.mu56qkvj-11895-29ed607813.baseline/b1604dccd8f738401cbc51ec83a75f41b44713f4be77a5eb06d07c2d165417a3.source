import unittest

from scripts.account_service import AccountService


class Adapter:
    def run(self, args):
        self.args = args
        return type("R", (), {"payload": {"credit_count": 12, "access_token": "secret"}, "exit_code": 0})()


class AccountServiceTests(unittest.TestCase):
    def test_account_returns_redacted_credit_payload(self):
        adapter = Adapter()
        result = AccountService(adapter).user_credit()
        self.assertEqual(adapter.args, ["user_credit"])
        self.assertEqual(result["credit_count"], 12)
        self.assertEqual(result["access_token"], "[REDACTED]")
