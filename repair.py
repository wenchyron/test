"""Stage a deterministic fix for the intentional synthetic regression only."""
import argparse, hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path
from verify import EVIDENCE, SHA, git

REPO = "wenchyron/test"
KEY = hashlib.sha256(f"{REPO}\nops.slot-refresh\nfresh-reply-stock-regression".encode()).hexdigest()
STOCK = "fixtures/ops-stock.py"
TEST = "tests/test_repaired_stock.py"
TEST_CODE = """import subprocess, sys, unittest
from pathlib import Path
class Stock(unittest.TestCase):
    def test_six_slots(self):
        stock = Path(__file__).resolve().parents[1] / "fixtures/ops-stock.py"
        self.assertEqual(subprocess.check_output([sys.executable, str(stock)]).strip(), b"6")
"""

def cmd(*args, cwd=None, data=None):
    return subprocess.run(args, cwd=cwd, input=data, text=True, capture_output=True,
                          check=True, timeout=90).stdout

def approved(r):
    bad = {**EVIDENCE["ops"], "slot_count": 5}
    control, source = (Path(__file__).parent / "fixtures/approved-sources.txt").read_text().split()
    expected = [("first_failure", source, bad), ("candidate_retry", source, bad),
                ("known_good", control, EVIDENCE["ops"])]
    observed = [(x["kind"], x["source_sha"], x["actual"]) for x in r.get("reproductions", [])]
    if (r.get("scope") != "synthetic" or r.get("environment") != "sandbox"
            or r.get("component") != "ops" or r.get("target") != "sandbox-cabinet"
            or r.get("fingerprint") != KEY or not r.get("eligible_for_repair")
            or r.get("requested", {}).get("source_sha") != source or observed != expected
            or [x["result"] for x in r["checks"]] != ["通过", "失败"]):
        raise ValueError("ineligible receipt")
    return source, control

def proof(source, control, patch):
    return dict(source=source, control=control, before=5, after=6,
                test="test_six_slots", fingerprint=KEY,
                patch_sha256=hashlib.sha256(patch.encode()).hexdigest())

def run_test(cwd):
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                           "-p", "test_repaired_stock.py"], cwd=cwd, capture_output=True, text=True, timeout=30)

def prepare(directory, r):
    source, control = approved(r)
    with tempfile.TemporaryDirectory(prefix="release-fix-") as temp:
        tree = Path(temp) / "checkout"
        cmd("git", "worktree", "add", "--detach", str(tree), source)
        try:
            stock = tree / STOCK
            if stock.read_text() != "print(5)\n":
                raise ValueError("unexpected fixture")
            (tree / TEST).parent.mkdir(exist_ok=True)
            (tree / TEST).write_text(TEST_CODE)
            before = run_test(tree)
            if before.returncode != 1 or "b'5' != b'6'" not in before.stderr:
                raise ValueError("no exact pre-fix assertion")
            stock.write_text("print(6)\n")
            after = run_test(tree)
            if after.returncode != 0 or "Ran 1 test" not in after.stderr:
                raise ValueError("post-fix assertion not green")
            cmd("git", "add", STOCK, TEST, cwd=tree)
            patch = cmd("git", "diff", "--cached", "--binary", cwd=tree)
            (directory / "candidate.patch").write_text(patch)
            (directory / "repair-proof.json").write_text(json.dumps(proof(source, control, patch), indent=2) + "\n")
        finally:
            cmd("git", "worktree", "remove", "--force", str(tree))

if __name__ == "__main__":
    path = Path(sys.argv[1]).resolve()
    prepare(path, json.loads((path / "receipt.json").read_text()))
