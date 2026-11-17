"""University list pagination must not change institution/authentication boundaries."""
import shutil
import subprocess
from pathlib import Path


def test_institution_plant_pagination_and_identity_boundaries():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([shutil.which('node') or 'node', 'tests/institution_pagination_checks.cjs'],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_audit_log_pagination_and_platform_role_boundaries():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([shutil.which('node') or 'node', 'tests/audit_pagination_checks.cjs'],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
