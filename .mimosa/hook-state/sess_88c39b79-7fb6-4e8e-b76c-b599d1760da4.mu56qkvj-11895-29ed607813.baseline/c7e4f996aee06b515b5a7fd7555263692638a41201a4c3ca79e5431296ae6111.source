import json
import tempfile
import unittest
from pathlib import Path
from scripts.diagnostic_service import DiagnosticService

class DiagnosticServiceTests(unittest.TestCase):
    def test_reads_regular_logs_and_redacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'a.log').write_text('Authorization: Bearer secret\nvisible')
            result=DiagnosticService(root).diagnose(command='dreamina version',error='failed',since_minutes=30,max_files=3)
            body=json.dumps(result); self.assertIn('visible',body); self.assertNotIn('secret',body)
    def test_skips_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); outside=root.parent/'outside-dreamina.log'; outside.write_text('outside-secret')
            self.addCleanup(lambda: outside.unlink(missing_ok=True)); (root/'bad.log').symlink_to(outside)
            result=DiagnosticService(root).diagnose(command='x',error='y',since_minutes=30,max_files=3)
            self.assertNotIn('outside-secret',json.dumps(result))
