"""Shared-session frontend contracts alongside the existing scientific checks."""
import shutil
import subprocess
import unittest

from src.hydroponic.config import ROOT


class IntegratedFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the frontend contract check')
    def test_shared_session_navigation_csrf_and_email_controls(self):
        result = subprocess.run(
            ['node', 'tests/integrated_frontend_checks.cjs'],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
