"""Hosted rehearsal only; no live services, credentials or devices."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

case = os.environ["GITHUB_REF_NAME"].split("/", 1)[-1]
if case not in {"normal", "unavailable", "mismatch", "regression"}:
    raise ValueError("unknown scenario")
out = Path("out")
out.mkdir(exist_ok=True)
start = dt.datetime.now(dt.timezone.utc).isoformat()
# Deliberate hold provides an observable interval for queue contention.
time.sleep(15)
good, bad = Path("fixtures/approved-sources.txt").read_text().split()
source = bad if case == "regression" else good
fixture = json.loads(subprocess.check_output(["git", "show", f"{source}:fixtures/release.json"]))
components = list(fixture["components"]) if case == "normal" else ["ops"]
expect = {"normal": ["通过", "通过"], "unavailable": ["未验证", "未验证"],
          "mismatch": ["失败", "未验证"], "regression": ["通过", "失败"]}[case]
results = {}
for component in components:
    row = fixture["components"][component]
    path = out / component
    rc = subprocess.run([sys.executable, "verify.py", "--component", component,
                         "--environment", "sandbox", "--target", "sandbox-cabinet",
                         "--version", "wrong-version" if case == "mismatch" else row["version"],
                         "--artifact", row["artifact"], "--source", source, "--baseline", good,
                         "--fault", "unavailable" if case == "unavailable" else "none",
                         "--out", str(path), "--lock-dir", os.path.join(os.environ["RUNNER_TEMP"], "rehearsal-lock")]).returncode
    receipt = json.loads((path / "receipt.json").read_text())
    assert rc == (0 if case == "normal" else 1)
    assert [item["result"] for item in receipt["checks"]] == expect
    assert receipt["eligible_for_repair"] is (case == "regression")
    if case == "regression":
        subprocess.run([sys.executable, "repair.py", str(path)], check=True)
    results[component] = expect
(out / "scenario.json").write_text(json.dumps({"case": case, "results": results,
    "started_at": start, "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "run_id": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"]},
    ensure_ascii=False, indent=2) + "\n")
