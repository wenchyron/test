import subprocess
import sys
import unittest
from pathlib import Path

from version_check import same_version


class SameVersionTests(unittest.TestCase):
    def test_prefix_is_not_a_match(self):
        self.assertFalse(same_version("21280", "2128"))

    def test_exact_match(self):
        self.assertTrue(same_version("2128", "2128"))

    def test_mismatch(self):
        self.assertFalse(same_version("2129", "2128"))

    def test_empty_values(self):
        for observed, expected in (("", "2128"), ("2128", ""), ("", "")):
            with self.subTest(observed=observed, expected=expected):
                self.assertFalse(same_version(observed, expected))

    def test_cli_output_and_exit_status(self):
        script = Path(__file__).with_name("version_check.py")
        for observed, expected, output, status in (
            ("2128", "2128", "accepted\n", 0),
            ("2129", "2128", "rejected\n", 1),
            ("", "2128", "rejected\n", 1),
        ):
            with self.subTest(observed=observed, expected=expected):
                result = subprocess.run(
                    [sys.executable, str(script), observed, expected],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.stdout, output)
                self.assertEqual(result.returncode, status)


if __name__ == "__main__":
    unittest.main()
