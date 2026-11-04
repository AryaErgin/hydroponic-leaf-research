"""Run the same real JS gesture handlers in the Python regression suite."""
from pathlib import Path
import subprocess
import unittest


class TestMobileGestures(unittest.TestCase):
    def test_native_coordinates_and_transactional_touch_tools(self):
        result = subprocess.run(
            ["node", "tests/mobile_gesture_checks.cjs"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
