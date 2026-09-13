import unittest
import tempfile
from pathlib import Path
from scripts.native_approval import ApprovalDeniedError

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

    def test_installer_rejects_unapproved_final_host(self):
        class Downloader:
            def fetch(self,url,max_bytes): return b'#!/bin/sh\n', 'https://evil.example/cli'
        with self.assertRaises(ValueError): EnvironmentService(Adapter(),downloader=Downloader()).install_or_upgrade('install',approval_provider=object())

    def test_installer_denial_never_runs(self):
        class Downloader:
            def fetch(self,url,max_bytes): return b'#!/bin/sh\n', url
        class Provider:
            def confirm(self,request): raise ApprovalDeniedError('denied')
        class Runner:
            calls=[]
            def run(self,path): self.calls.append(path); return 0,'',''
        runner=Runner()
        with self.assertRaises(ApprovalDeniedError): EnvironmentService(Adapter(),downloader=Downloader(),runner=runner).install_or_upgrade('upgrade',approval_provider=Provider())
        self.assertEqual(runner.calls,[])
