#!/usr/bin/env python3
"""Manual release verification. Fixture and target evidence never mix."""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


CHECKS = {
    "pos": ("pos.rental-recovery", "correct recovery; no duplicate dispense", "rental / AT 204"),
    "at": ("at.slot-read", "reply for this station and request, correct slot count", "AT / MQTT / slot board"),
    "advision": ("advision.playback", "two distinct approved frames; correct order", "playlist / media"),
    "ops": ("ops.slot-refresh", "fresh stock reply; not stale cached detail", "Ops / upstream slot read"),
}
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
SAFE = re.compile(r"[A-Za-z0-9_.-]{1,120}\Z")
EVIDENCE = {
    "pos": {"same_station": True, "timeout_returns_home": True, "next_rental_dispenses": 1},
    "at": {"same_station": True, "fresh_reply": True, "slots": 6},
    "advision": {"distinct_approved_frames": 2, "playlist_order": ["A", "B"]},
    "ops": {"same_station": True, "fresh_reply": True, "slot_count": 6},
}

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def git(*args):
    return subprocess.run(["git", *args], check=True, text=True, capture_output=True, timeout=15).stdout.strip()

def load_fixture(sha):
    if not SHA.fullmatch(sha) or git("rev-parse", f"{sha}^{{commit}}") != sha:
        raise ValueError("source_commit_not_found")
    return json.loads(git("show", f"{sha}:fixtures/release.json"))

def row(check, state, expected, actual, step, reason=None, evidence=None):
    return dict(check=check, result=state, expected=expected, actual=actual,
                failure_step=step, reason=reason, evidence=evidence)

def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

@contextlib.contextmanager
def lock(lock_dir, name, seconds=60):
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (lock_dir / (name + ".lock")).open("a") as handle:
        deadline = time.monotonic() + seconds
        waited = time.monotonic()
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("station_lease_unavailable")
                time.sleep(.05)
        yield round(time.monotonic() - waited, 3)

def probe(sha, component, fixture):
    observed = EVIDENCE[component].copy()
    if component == "ops":
        # The sandbox's known-good/known-bad source is executed separately by git SHA.
        # Production must replace this fixture with real deployed-source identity and an approved smoke adapter.
        payload = git("show", f"{sha}:fixtures/ops-stock.py")
        value = subprocess.run([sys.executable, "-I", "-c", payload], check=True, capture_output=True,
                               text=True, timeout=10).stdout.strip()
        observed["slot_count"] = int(value)
    return observed

def safe_expected_probes(actual, expected):
    return (type(actual) is dict and set(actual) == set(expected)
            and all(type(actual[key]) is type(value) for key, value in expected.items())
            and all(len(value) <= 20 and all(isinstance(x, str) and re.fullmatch(r"[A-Z]", x) for x in value)
                    for value in actual.values() if isinstance(value, list)))


def evaluate(args, out):
    identifier = args.artifact
    check, description, interface = CHECKS[args.component]
    expected = dict(version=args.version, source_sha=args.source, artifact=identifier)
    record = dict(schema=1, component=args.component, environment=args.environment, target=args.target,
                  scope="synthetic" if args.environment == "sandbox" else "target",
                  affected_interface=interface, requested=expected, observed=None,
                  run_id=args.run_id, attempt=args.attempt, started_at=utc(), operations=[],
                  reproductions=[], fingerprint=None, eligible_for_repair=False, checks=[])
    if args.environment != "sandbox" and args.fault != "none":
        raise ValueError("fault_injection_is_sandbox_only")
    if args.environment == "sandbox" and args.target != "sandbox-cabinet":
        raise ValueError("unexpected_sandbox_target")
    if args.component != "ops" and not DIGEST.fullmatch(identifier):
        raise ValueError("android_artifact_must_be_sha256")
    if args.component == "ops" and not SAFE.fullmatch(identifier):
        raise ValueError("invalid_ops_deployment_id")
    if not SHA.fullmatch(args.source) or not SAFE.fullmatch(args.version):
        raise ValueError("invalid_release_identity")
    if args.environment != "sandbox":
        # Real target must be selected and attested. No fallback to fixtures.
        record["checks"] = [
            row("release.identity", "未验证", expected, None, "target", "approved_target_not_configured"),
            row(check, "未验证", description, None, "identity", "identity_unverified"),
        ]
        return record
    allowed = set((Path(__file__).parent / "fixtures/approved-sources.txt").read_text().split())
    if args.source not in allowed or (args.baseline and args.baseline not in allowed):
        raise ValueError("fixture_source_not_pinned")
    fixture = load_fixture(args.source)
    release = fixture["components"][args.component]
    observed = dict(version=release["version"], source_sha=args.source, artifact=release["artifact"])
    record["observed"] = observed
    if args.fault == "unavailable":
        record["checks"] = [
            row("release.identity", "未验证", expected, None, "target", "target_unavailable"),
            row(check, "未验证", description, None, "identity", "identity_unverified"),
        ]
        return record
    if expected != observed:
        record["checks"] = [
            row("release.identity", "失败", expected, observed, "identity", "version_mismatch", "receipt.json"),
            row(check, "未验证", description, None, "identity", "identity_mismatch"),
        ]
        return record
    record["checks"].append(row("release.identity", "通过", expected, observed, None, evidence="receipt.json"))
    desired = EVIDENCE[args.component]
    actual = probe(args.source, args.component, fixture)
    success = type(actual) is dict and actual == desired
    record["checks"].append(row(check, "通过" if success else "失败", desired, actual,
                                None if success else "business_assertion",
                                None if success else "expected_value_mismatch", "receipt.json"))
    if success:
        return record
    repeat = probe(args.source, args.component, fixture)
    record["reproductions"] = [
        dict(kind="first_failure", source_sha=args.source, expected=desired, actual=actual),
        dict(kind="candidate_retry", source_sha=args.source, expected=desired, actual=repeat),
    ]
    if not args.baseline or args.baseline == args.source:
        return record
    old = load_fixture(args.baseline)
    control = probe(args.baseline, args.component, old)
    record["reproductions"].append(dict(kind="known_good", source_sha=args.baseline, expected=desired, actual=control))
    # Allow only the intentional Ops test failure to enter the sandbox publisher.
    # Never let caller-provided text or logs drive code changes or GitHub writes.
    if (args.component == "ops" and actual == repeat and control == desired
            and actual == {**desired, "slot_count": 5}
            and fixture.get("control") == args.baseline):
        stable = ["wenchyron/test", check, "fresh-reply-stock-regression"]
        record["fingerprint"] = hashlib.sha256("\n".join(stable).encode()).hexdigest()
        record["eligible_for_repair"] = True
    return record

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--component", choices=CHECKS, required=True)
    p.add_argument("--environment", choices=["sandbox", "isolated-avd", "test-cabinet", "ops-test"], required=True)
    p.add_argument("--target", required=True, type=lambda x: x if SAFE.fullmatch(x) else p.error("invalid target key"))
    p.add_argument("--runner-config", type=Path)
    for name in ("version", "source", "artifact"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--baseline", default="")
    p.add_argument("--fault", choices=["none", "unavailable"], default="none")
    p.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID", "local"))
    p.add_argument("--attempt", default=os.getenv("GITHUB_RUN_ATTEMPT", "1"))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--lock-dir", type=Path, required=True)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / "receipt.json").exists():
        p.error("evidence already exists; use a fresh directory for a rerun")
    try:
        checked_at = utc()
        with lock(args.lock_dir, args.target) as waited:
            acquired = utc()
            record = evaluate(args, args.out)
            released = utc()
        record["lease"] = dict(target=args.target, requested_at=checked_at, acquired_at=acquired,
                               released_at=released, seconds_waited=waited)
    except (ValueError, OSError, TimeoutError, KeyError, subprocess.SubprocessError) as error:
        # Never publish exception content: it can embed target names or command output.
        record = dict(schema=1, scope="synthetic" if args.environment == "sandbox" else "target",
                      component=args.component, environment=args.environment, target=args.target,
                      run_id=args.run_id, attempt=args.attempt,
                      eligible_for_repair=False,
                      checks=[row("release.identity", "未验证", "resolvable release identity", None, "preflight", type(error).__name__),
                              row(CHECKS[args.component][0], "未验证", CHECKS[args.component][1], None, "identity", "identity_unverified")])
    record["finished_at"] = utc()
    write(args.out / "receipt.json", record)
    summary = [f"## {record['component']} · {record['environment']} ({record['scope']})",
               f"GitHub run {record['run_id']}, attempt {record['attempt']}", ""]
    summary += [f"- {c['check']}: **{c['result']}** {c['reason'] or ''}" for c in record["checks"]]
    report = "\n".join(summary) + "\n"
    (args.out / "summary.md").write_text(report)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as handle:
            handle.write(report)
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
            handle.write(f"repair={str(record['eligible_for_repair']).lower()}\n")
    print(report)
    return 0 if all(c["result"] == "通过" for c in record["checks"]) else 1

if __name__ == "__main__":
    raise SystemExit(main())
