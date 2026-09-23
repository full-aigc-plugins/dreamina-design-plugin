"""Browser lease ownership and bounded signature checks."""
import gc
import os
import tempfile
import unittest
from unittest.mock import patch
from scripts.trusted_media_tools import BrowserLaunchLease,TrustedMediaToolStore,TrustedMediaToolError
from scripts.bounded_process import BoundedProcessTimeout

class BrowserLeaseRoundTwoTests(unittest.TestCase):
    def test_abandoned_lease_closes_both_handles(self):
        with tempfile.TemporaryDirectory() as directory:
            a=os.open(directory,os.O_RDONLY|os.O_DIRECTORY); b=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            lease=BrowserLaunchLease(a,b,None,'Contents/MacOS/Browser')
            del lease; gc.collect()
            for fd in (a,b):
                with self.assertRaises(OSError): os.fstat(fd)

    def test_signature_timeout_remains_bounded_and_typed(self):
        calls=[]
        def timeout(argv,**kwargs):
            calls.append(kwargs)
            raise BoundedProcessTimeout('deadline')
        with patch('scripts.trusted_media_tools.run_bounded',timeout):
            with self.assertRaises(TrustedMediaToolError):
                TrustedMediaToolStore()._verify_browser_signature('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        self.assertEqual(calls[0]['timeout_seconds'],60)
        self.assertEqual(calls[0]['stdout_cap'],65536)
        self.assertEqual(calls[0]['stderr_cap'],65536)
