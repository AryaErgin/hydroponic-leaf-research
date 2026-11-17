import subprocess
import unittest
from src.hydroponic.config import ROOT

class TestLeafFrontend(unittest.TestCase):
    def test_actual_canvas_operations_and_native_payload(self):
        r=subprocess.run(['node','tests/leaf_editor_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_capture_date_change_preserves_assignments_on_same_analysis(self):
        r=subprocess.run(['node','tests/leaf_daily_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_professor_approval_transmits_reviewed_labels_and_blocks_unassigned_or_other_plant(self):
        r=subprocess.run(['node','tests/leaf_approval_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
