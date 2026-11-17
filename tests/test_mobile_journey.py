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

    def test_retained_work_session_reconnect(self):self.check("ux_session_checks.cjs")

    def test_history_does_not_mix_measurement_targets(self):self.check("history_view_checks.cjs")

    def test_calibration_explains_missing_diameter_with_retained_points(self):self.check("calibration_guidance_checks.cjs")

    def test_only_adjacent_review_confirmation_redundancy_is_hidden(self):self.check("approval_guidance_checks.cjs")
