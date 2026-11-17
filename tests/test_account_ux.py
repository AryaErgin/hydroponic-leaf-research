"""Purpose-specific account navigation and recoverable client-state contracts."""
import shutil
import subprocess
import unittest
from src.hydroponic.config import ROOT
class AccountUXTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed for frontend handlers')
    def test_account_purpose_recovery_and_pending_navigation(self):
        result = subprocess.run(['node', 'tests/account_ux_checks.cjs'], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
