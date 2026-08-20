"""Compare request latency: one-shot runner vs persistent session.

Runs against the PRODUCTION database (read-only). Measures:

* fresh-process runner: wall time to process ONE request (startup + full
  repository load + execution);
* persistent session: process start -> first response (startup + load +
  execution), then 2nd and 3rd request latencies (no reload).

Expected result: the session's first request pays the load cost and
subsequent requests ride the already-loaded repository. This script prints a
small table; it is intentionally not part of the unittest suite (timing is
reported, not asserted).

Usage:
    python benchmarks/benchmark_session.py
"""

import json
import os
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCTION_DB = os.path.join(_ROOT, "database", "knowledge.db")


def _time_ms(s):
    return round(s * 1000.0, 1)


def fresh_runner_benchmark(python):
    """One-shot runner: one request, full startup + load included."""
    t0 = time.monotonic()
    proc = subprocess.run(
        [python, "-m", "api.tools", "-"], cwd=_ROOT,
        input=json.dumps({"operation": "inspect"}),
        capture_output=True, text=True, timeout=300)
    dt = time.monotonic() - t0
    payload = json.loads(proc.stdout)
    assert payload.get("ok"), "one-shot runner failed"
    return dt, payload["result"]["node_count"]


def session_benchmark(python):
    """Persistent session: first vs subsequent request latency."""
    t0 = time.monotonic()
    proc = subprocess.Popen(
        [python, "-m", "api.session", "--db", PRODUCTION_DB],
        cwd=_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1)
    assert proc.stdin is not None and proc.stdout is not None
    assert proc.stderr is not None
    spawn_to_first = time.monotonic()

    def ask(request, t_mark):
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        assert line, "session closed unexpectedly"
        elapsed = time.monotonic() - t_mark
        return elapsed, line

    first_latency, _ = ask({"operation": "inspect"}, t0)
    spawn_to_first_ms = _time_ms(spawn_to_first - t0)

    second_latency, line2 = ask({"operation": "inspect"},
                                time.monotonic())
    third_latency, _ = ask({"operation": "inspect"}, time.monotonic())

    assert json.loads(line2).get("ok")
    assert proc.stdin is not None and proc.stdout is not None
    assert proc.stderr is not None
    proc.stdin.close()
    rc = proc.wait(timeout=300)
    err = proc.stderr.read()
    proc.stdout.close()
    proc.stderr.close()
    stats = {}
    for line in err.splitlines():
        if line.startswith("SESSION stats=") and line.endswith(" end"):
            raw = line[len("SESSION stats="):-len(" end")]
            stats = json.loads(raw)
    return {
        "first_request_ms": _time_ms(first_latency),
        "second_request_ms": _time_ms(second_latency),
        "third_request_ms": _time_ms(third_latency),
        "spawn_to_first_ms": spawn_to_first_ms,
        "returncode": rc,
        "stats": stats,
    }


def main():
    python = sys.executable
    print("Production DB: %s (%s bytes exists)" % (
        PRODUCTION_DB, os.path.getsize(PRODUCTION_DB)))
    print("Database not modified by this benchmark (read-only operations).")

    fresh_dt, fresh_nodes = fresh_runner_benchmark(python)
    print("\n[1] One-shot runner (python -m api.tools)")
    print("    one request incl. startup + load: %s ms (node_count=%d)"
          % (_time_ms(fresh_dt), fresh_nodes))

    sess = session_benchmark(python)
    print("\n[2] Persistent session (python -m api.session)")
    print("    process start -> first response      : %s ms"
          % sess["spawn_to_first_ms"])
    print("    first request  (incl. load)          : %s ms"
          % sess["first_request_ms"])
    print("    second request (loaded already)      : %s ms"
          % sess["second_request_ms"])
    print("    third request  (loaded already)      : %s ms"
          % sess["third_request_ms"])
    print("    session return code                  : %s" % sess["returncode"])
    print("    stderr stats                        : %s" % sess["stats"])

    speedup = sess["first_request_ms"] / max(sess["second_request_ms"], 1e-6)
    print("\nSpeedup (first request / second request): ~%.1fx" % speedup)
    if sess["stats"].get("initializations") == 1:
        print("Repository initialized exactly once: YES")
    else:
        print("Repository initialized exactly once: NO (stats=%r)"
              % sess["stats"].get("initializations"))
    return 0


if __name__ == "__main__":
    sys.exit(main())