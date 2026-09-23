"""Execute the fixed screenshot proxy against adversarial owned browser fixtures."""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from scripts.reelbench_adapter import BROWSER_PROXY_CODE
from tests.test_reelbench_sync_recovery import png

class BrowserProxyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.work=self.root/'work'
        self.panels=self.work/'output/panels'
        self.panels.mkdir(parents=True)
        self.panels.chmod(0o700)
        (self.panels/'panel.html').write_text('<html><body>owned</body></html>')
        self.bundle=self.root/'Browser.app'
        self.browser=self.bundle/'Contents/MacOS/Browser'
        self.browser.parent.mkdir(parents=True)

    def launch(self, extra=''):
        payload=png(8,8)
        script='#!/usr/bin/python3\nimport os,sys\nfrom pathlib import Path\np=Path(next(a.split("=",1)[1] for a in sys.argv if a.startswith("--screenshot=")))\np.write_bytes(bytes.fromhex('+repr(payload.hex())+'))\np.chmod(0o600)\n'+extra
        if self.browser.exists(): self.browser.chmod(0o700)
        self.browser.write_text(script)
        self.browser.chmod(0o500)
        bundle=os.open(self.bundle,os.O_RDONLY|os.O_DIRECTORY)
        binary=os.open(self.browser,os.O_RDONLY)
        try:
            return subprocess.run(['/usr/bin/python3','-I','-c',BROWSER_PROXY_CODE.decode(),
                '--headless','--screenshot='+str(self.panels/'static.png'),(self.panels/'panel.html').as_uri()+'#static'],
                cwd=self.work,env={'PATH':'/usr/bin:/bin','REELBENCH_BROWSER_BUNDLE_FD':str(bundle),
                    'REELBENCH_BROWSER_EXECUTABLE_FD':str(binary),'REELBENCH_BROWSER_RELATIVE':'Contents/MacOS/Browser',
                    'REELBENCH_BROWSER_SHA256':hashlib.sha256(self.browser.read_bytes()).hexdigest()},
                pass_fds=(bundle,binary),capture_output=True,text=True,timeout=10)
        finally:
            os.close(binary)
            os.close(bundle)

    def test_copies_exact_png_and_removes_owned_temp(self):
        result=self.launch()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.panels/'static.png').read_bytes(),png(8,8))
        self.assertEqual(list(self.panels.glob('.browser-*')),[])

    def test_destination_collision_never_overwrites(self):
        target=self.panels/'static.png'
        target.write_bytes(b'keep')
        result=self.launch()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(target.read_bytes(),b'keep')
        self.assertEqual(list(self.panels.glob('.browser-*')),[])

    def test_temp_directory_replacement_is_rejected_without_publication(self):
        result=self.launch('old=p.parent\nold.rename(old.with_name(old.name+"-moved"))\nold.mkdir(mode=0o700)\np.write_bytes(b"foreign")\n')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('identity changed',result.stderr)
        self.assertFalse((self.panels/'static.png').exists())

    def test_cleanup_failure_is_nonzero_and_does_not_delete_unknown_file(self):
        result=self.launch('(p.parent/"unknown").write_bytes(b"keep")\n')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(len(list(self.panels.glob('.browser-*/unknown'))),1)

    def test_invalid_png_and_oversized_png_fail_before_copy(self):
        for extra in ('p.write_bytes(b"invalid")\n', 'with p.open("ab") as f: f.truncate(67108865)\n'):
            result=self.launch(extra)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((self.panels/'static.png').exists())
