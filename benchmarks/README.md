# benchmarks/

Public-facing micro-benchmarks. Everything under `_dev/` is gitignored
developer scratch; anything a README promises to be reproducible lives here.

## bench_latency.py

Measures p50 / p95 / p99 latency of the four core memory operations
(remember / SGM-entity search / keyword search / correct-supersede) against a
throwaway SQLite database in the system temp dir. The database is deleted at
the end of the run.

### Run

    python benchmarks/bench_latency.py --n 200

Requires the package importable: either run it from a repo checkout
(`pip install -e .` or have `gateway.py` / `memsearch.py` on the path), or
`pip install memtether`.

### Arguments

| arg | default | meaning |
|---|---|---|
| `--n` | 200 | iterations for remember / correct; search is capped at 50 |
| `--k` | 10 | result limit per search call |

### Output fields

Each row is one operation with `p50` / `p95` / `p99` in milliseconds, plus
`mean`. p50/p95/p99 are taken from a sorted latency list by index
(`lat[n//2]`, `lat[int(n*0.95)]`, `lat[int(n*0.99)]`) - plain nearest-rank,
no interpolation.

### Provenance of the numbers in the README

The p50 / p95 / p99 figures quoted in the README Performance section come
from this script with `--n 200`, run on the developer's machine
(Windows, Python 3.13, SQLite via Python's stdlib `sqlite3`, vector search
disabled via `MEM_SKIP_VECTOR=1`, throwaway temp DB). Re-running on other
hardware will give different absolute numbers; the relative ordering of the
four operations is the meaningful part.
