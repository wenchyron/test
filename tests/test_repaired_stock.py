import subprocess, sys, unittest
from pathlib import Path
class Stock(unittest.TestCase):
    def test_six_slots(self):
        stock = Path(__file__).resolve().parents[1] / "fixtures/ops-stock.py"
        self.assertEqual(subprocess.check_output([sys.executable, str(stock)]).strip(), b"6")
