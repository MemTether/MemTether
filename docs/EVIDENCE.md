# EVIDENCE.md — approved claims registry

Every number quoted in the README, PyPI description, blog posts, or social
media **must** appear here with its date, denominator, scoring rule, and
reproduction command. If a number is not in this table, do not cite it.

Inspired by Caura's EVIDENCE registry and MemPalace's tiered honest reporting —
this is the institutional layer on top of our `tests/test_docs_claims.py`
guard (which locks README numbers to code reality mechanically).

## Test suite

| Claim | Value | Date | Reproduce |
|---|---|---|---|
| pytest | 665 passed, 6 skipped | 2026-11-01 | `python -m pytest tests/ -q --tb=no` |
| Ruff (E9/F63/F7/F82) | clean | 2026-11-01 | `ruff check . --select E9,F63,F7,F82 --ignore E501` |
| CI | 9 jobs green (3 OS × 2 py + lint + build + docker) | 2026-11-01 | GitHub Actions on main |
| OpenSSF Best Practices | badge active (project 15441) | 2026-11-01 | https://www.bestpractices.dev/projects/15441 |

## Latency (microbenchmark, local SQLite, n=200)

| Claim | p50 | p95 | Date | Reproduce |
|---|---|---|---|---|
| remember (insert) | 33.48ms | 43.39ms | 2026-11-01 | `python benchmarks/bench_latency.py --n 200` |
| search (SGM entity) | 31.38ms | 49.20ms | 2026-11-01 | same |
| search (keyword) | 38.91ms | 43.59ms | 2026-11-01 | same |
| correct (supersede) | 64.98ms | 78.13ms | 2026-11-01 | same |

Machine-readable: `docs/bench_data_latest.json` (auto-refreshed weekly by the
Weekly Benchmarks workflow).

## LongMemEval (500 questions, full run, our harness)

| Claim | Value | Denominator | Date | Notes |
|---|---|---|---|---|
| Strict match (global) | 62.6% | 209/334 applicable | 2026-10-02 | |
| LLM judge (global) | 55.7% | 264/474 | 2026-10-02 | |
| EAF (retrieval variant) | 75.7% | 253/334 | 2026-10-02 | paired McNemar +19.4pp, p=7.8e-7 vs baseline on same questions |

**Allowed phrasing:** "75.7% under our EAF retrieval variant (removes
contradiction-induced context before answering)".
**Forbidden phrasing:** "75.7% — beats mem0" (different denominators, different
harness; that comparison has never been run end-to-end).

## LoCoMo (1,986 questions, public MemEval harness)

| Claim | Value | Date | Notes |
|---|---|---|---|
| Token F1 | 0.523 (rank 4/10) | 2026-10-14 | ahead of Hindsight 0.489, Graphiti 0.416, mem0 0.344; behind PropMem 0.605, OpenClaw 0.557 |

**Allowed phrasing:** "ranks 4/10 on token-F1, ahead of Hindsight/mem0/SimpleMem/MemU".
**Forbidden phrasing:** any claim of SOTA.

## Governance

| Claim | Date | Reproduce |
|---|---|---|
| Hash-chain tamper detection | 2026-11-01 | `python examples/audit_chain_demo.py` (PASS → tamper → FAIL) |
| Chain verify on live DB | 2026-11-01 | `memtether court --verify` |
| Causal finding: cross-agent contradictions cost up to 73pp downstream | 2026-10 | `experiments/reproducibility/` (JSON records committed) |

## Adoption (honest numbers)

| Claim | Value | Date | Source |
|---|---|---|---|
| PyPI downloads | ~1,322/month (de-mirrored) | 2026-10-30 | pypistats |
| GitHub traffic | 5,662 clones / 967 unique in 14 days | 2026-10 | GitHub Traffic API |
| Stars | see repo badge (currently small — do not inflate) | live | GitHub |
