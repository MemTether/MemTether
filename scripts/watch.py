#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watch.py — session-boundary capture → governed memory (Wave 2 / B1 v1).

Purpose
-------
"Write coverage is the system's job, not the agent's judgement."
(agentmemory validates this with 6 hooks/agent; we start with the ingest
half and leave hook installation to per-harness adapters later.)

Usage
-----
    # one-shot: extract from a transcript file now
    python scripts/watch.py ingest --file session.jsonl --source claude-code

    # one-shot on a plain text transcript
    python scripts/watch.py ingest --file notes.txt --source codex --format text

    # dry-run: show what would be written, write nothing
    python scripts/watch.py ingest --file session.jsonl --source claude-code --dry-run

What it extracts (v1 heuristics, deliberately narrow):
  - decisions:    lines starting with 决定：/决定:/Decision:/decided to/we'll use
  - lessons:      lines containing 失败/坑/bug/fix/workaround/lesson/根因
  - everything else is ignored — better to miss than to flood the store
    with noise (the near-dup gate catches repeats, but precision comes
    from the extraction rules).

All writes go through gateway.remember → near-dup gate + hash chain +
mandatory source. Nothing bypasses governance.

Honest limitation (v1): batch-at-boundary. A live tailing daemon is v2
and needs a decision about long-running processes on this platform.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

DECISION_PAT = re.compile(
    r"^\s*(?:[-*]?\s*)?(决定[：:]|Decision[:\s]|decided to |we(?:'| a)re going to |use |switch(?:ed)? to )",
    re.I)
LESSON_PAT = re.compile(
    r"(失败|坑|根因|bug|fix(?:ed)?|workaround|lesson|gotcha|mistake|浪费|白做|回滚|revert)", re.I)
NOISE_PAT = re.compile(r"^\s*(\[|\{|#|//|$)", re.A)


def iter_lines(path, fmt):
    """Yield (lineno, text) from a transcript file. JSONL-aware."""
    opener = open(path, encoding="utf-8", errors="replace")
    with opener as f:
        if fmt == "text":
            for i, line in enumerate(f, 1):
                yield i, line.rstrip()
            return
        # jsonl: try to pull the interesting text field per record
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                yield i, line
                continue
            # Claude Code / generic transcript shapes
            for key in ("text", "content", "message", "summary", "output"):
                v = rec.get(key)
                if isinstance(v, str) and v.strip():
                    yield i, v.strip()
                    break
                if isinstance(v, list):
                    parts = [b.get("text", "") for b in v if isinstance(b, dict) and b.get("text")]
                    if parts:
                        yield i, " ".join(parts)
                        break
                if isinstance(v, dict):
                    inner = v.get("text") or v.get("content")
                    if isinstance(inner, str) and inner.strip():
                        yield i, inner.strip()
                        break


def extract(lines, max_len=300):
    """Return candidate (kind, text) tuples. Narrow rules, precision-first."""
    out, seen = [], set()
    for lineno, text in lines:
        t = text.strip()
        if not t or len(t) < 8 or len(t) > 2000:
            continue
        if NOISE_PAT.match(t) and "：" not in t and ":" not in t[:12]:
            continue
        kind = None
        if DECISION_PAT.match(t):
            kind = "decision"
        elif LESSON_PAT.search(t):
            kind = "experience"
        if not kind:
            continue
        # one sentence max
        t = t.splitlines()[0][:max_len].strip()
        key = t.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append((kind, t, lineno))
    return out


def cmd_ingest(args):
    path = os.path.abspath(args.file)
    if not os.path.exists(path):
        print("transcript not found:", path)
        return 1
    lines = iter_lines(path, args.format)
    candidates = extract(lines, max_len=args.max_len)
    print("extracted %d candidate(s) from %s" % (len(candidates), os.path.basename(path)))
    if not candidates:
        return 0
    if args.dry_run:
        for kind, text, lineno in candidates:
            print("  [%s @L%d] %s" % (kind, lineno, text[:120]))
        print("dry-run: nothing written")
        return 0

    import gateway
    written, skipped = 0, 0
    for kind, text, lineno in candidates:
        try:
            r = gateway.remember(text, type=kind, source=args.source)
            if isinstance(r, dict) and r.get("ok"):
                written += 1
            else:
                skipped += 1
        except Exception as e:
            skipped += 1
            print("  [skip @L%d] %s" % (lineno, str(e)[:100]))
    print("governed writes: %d written, %d skipped (dedup/validation)" % (written, skipped))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="watch", description=__doc__.split("Usage")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("ingest", help="extract governed memories from a transcript file")
    sp.add_argument("--file", required=True, help="transcript path (.jsonl or text)")
    sp.add_argument("--source", required=True, help="source attribution (e.g. claude-code)")
    sp.add_argument("--format", choices=["jsonl", "text"], default="jsonl")
    sp.add_argument("--max-len", type=int, default=300, help="max memory length")
    sp.add_argument("--dry-run", action="store_true", help="show candidates without writing")
    sp.set_defaults(func=cmd_ingest)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
