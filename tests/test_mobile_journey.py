import shutil,subprocess,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class TestMobileJourney(unittest.TestCase):
    def check(self,name):
        result=subprocess.run([shutil.which('node') or '/mnt/c/Program Files/nodejs/node.exe','tests/'+name],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_mobile_workflow_controls_and_messages(self):self.check('mobile_journey_checks.cjs')
    def test_actual_async_journey_handlers(self):self.check('mobile_journey_handlers_checks.cjs')

    def test_iphone_preview_timeout(self):self.check("iphone_preview_timeout_checks.cjs")

    def test_iphone_session_recovery(self):self.check("iphone_session_checks.cjs")

    def test_guided_state_policy(self):self.check("workflow_state_checks.cjs")

    def test_sanitized_photo_diagnostics(self):self.check("image_diagnostics_checks.cjs")
