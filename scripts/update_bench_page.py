#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""update_bench_page.py — merge fresh bench results into docs/benchmark.html.

Reads /tmp/latency_results.json (written by benchmarks/bench_latency.py),
stamps docs/benchmark.html with commit + date, and writes a machine-readable
docs/bench_data_latest.json for the live-page pipeline.

Wave1.5/B3 (three-scan plan): hindsight has a live benchmark page; ours is a
static page that silently rots. This script + the weekly workflow make the
page's "Last updated" honest.
"""
import argparse, json, os, subprocess, sys, datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(REPO, "docs", "benchmark.html")
DATA = os.path.join(REPO, "docs", "bench_data_latest.json")


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=REPO,
                              capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="/tmp/latency_results.json")
    ap.add_argument("--tests", default="")
    args = ap.parse_args()

    if not os.path.exists(args.results):
        print("results file missing:", args.results)
        return 1
    results = json.load(open(args.results, encoding="utf-8"))
    commit = git_commit()
    today = datetime.date.today().isoformat()
    payload = {
        "updated": today,
        "commit": commit,
        "tests": args.tests,
        "latency": results,
    }
    json.dump(payload, open(DATA, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    if os.path.exists(PAGE):
        body = open(PAGE, encoding="utf-8").read()
        stamp = (
            '<p style="color:#8b949e;font-size:0.9em">Last updated: %s · commit '
            '<code>%s</code> · regenerate: <code>python benchmarks/bench_latency.py --n 200</code>'
            ' · data: <code>docs/bench_data_latest.json</code></p>' % (today, commit)
        )
        marker = '<h1>MemTether Benchmarks</h1>'
        if marker in body:
            # replace any previous stamp block
            import re
            body = re.sub(
                r'<p style="color:#8b949e;font-size:0\.9em">Last updated:.*?</p>',
                '', body, flags=re.S)
            body = body.replace(marker, marker + stamp, 1)
            open(PAGE, "w", encoding="utf-8", newline="").write(body)
            print("stamped docs/benchmark.html (%s @ %s)" % (today, commit))
        else:
            print("marker not found in benchmark.html — page unchanged, data JSON still written")
    else:
        print("docs/benchmark.html not found — data JSON still written")
    print("data:", DATA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
