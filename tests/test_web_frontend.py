import shutil
import subprocess
import unittest
from src.hydroponic.config import ROOT


class TestFrontend(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed for calibration UI handler regression')
    def test_calibration_confirmation_and_geometry_change_handlers(self):
        result=subprocess.run(['node','tests/calibration_handlers_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for calibration reuse UI regression')
    def test_calibration_reuse_preview_and_confirmation_preserve_editor(self):
        result=subprocess.run(['node','tests/calibration_reuse_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for cold analysis deadline regression')
    def test_cold_analysis_deadline_preserves_approval_timeout(self):
        result=subprocess.run(['node','tests/leaf_analysis_timeout_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for identity archive recovery regressions')
    def test_draft_recovers_archive_without_losing_undo_or_leaving_stale_error(self):
        result=subprocess.run(['node','tests/leaf_identity_registry_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for Add transfer pointer regressions')
    def test_add_transfers_only_explicit_lamina_selection_and_undo_redo(self):
        result=subprocess.run(['node','tests/leaf_add_transfer_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for native leaf identity/numbering regressions')
    def test_contiguous_leaf_numbering_identity_and_undo_redo(self):
        result=subprocess.run(['node','tests/leaf_numbering_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed only for frontend regressions')
    def test_date_change_preserves_native_draft_and_resets_approval(self):
        result=subprocess.run(['node','tests/web_daily_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed only for the frontend race regression')
    def test_delayed_plant_selection_cannot_restore_another_plants_review(self):
        result=subprocess.run(['node','tests/web_frontend_checks.cjs'],cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':unittest.main()
