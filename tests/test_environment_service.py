import unittest

from scripts.environment_service import EnvironmentService


class Adapter:
    def __init__(self): self.calls = []
    def capability_snapshot(self): return {"cli_version": "1.4.18", "modes": ["text2image"], "captured_at": "now", "models": []}
    def run_text(self, args):
        self.calls.append(args)
        return type("R", (), {"exit_code": 0, "stdout": "Usage: dreamina", "stderr": ""})()


class EnvironmentServiceTests(unittest.TestCase):
    def test_status_reports_version_and_selected_help(self):
        adapter = Adapter()
        result = EnvironmentService(adapter).status(command="image_upscale", detail="summary")
        self.assertEqual(result["installed"], True)
        self.assertEqual(result["command"], "image_upscale")
        self.assertEqual(adapter.calls, [["image_upscale", "--help"]])

    def test_status_rejects_unknown_command(self):
        with self.assertRaises(ValueError): EnvironmentService(Adapter()).status(command="shell", detail="summary")
