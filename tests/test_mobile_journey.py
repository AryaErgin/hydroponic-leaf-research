import shutil,subprocess,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class TestMobileJourney(unittest.TestCase):
    def check(self,name):
        result=subprocess.run([shutil.which('node') or '/mnt/c/Program Files/nodejs/node.exe','tests/'+name],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_mobile_workflow_controls_and_messages(self):self.check('mobile_journey_checks.cjs')
    def test_actual_async_journey_handlers(self):self.check('mobile_journey_handlers_checks.cjs')
