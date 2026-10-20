# Roadmap

MemTether is under active development. This document tracks what is
planned, in progress, and deliberately out of scope.

## Near-term (next minor release)

- [ ] Auto-anchor performance benchmark (still unmeasured — the only remaining
      near-term item; the three below moved to Completed in a82)
- [ ] Hub-side test coverage for the remaining CLI surface
      (`mem.py cmd_search`/`cmd_asof`/`cmd_qvalue`/`cmd_distill`,
      `embed_local`, `skill_forge`) — hub suite went 0 → 125 tests this cycle
      but these modules are still uncovered

## Completed (a82)

- [x] **Layered recall (L0/L1/L2)** — `memsearch.content_layers()` /
      `apply_layers()`, deterministic split with zero LLM and zero new tables.
      `gateway.search(layer=...)` and `memtether search --layer L0|L1|L2`.
      Measured on a 2,221-fact production DB (limit=5): L0 = 10% of L2's
      characters, L1 = 44%. L2 (default) is byte-identical to before.
- [x] **Deterministic subject profile** — `gateway.profile()` aggregates one
      subject's active memories (counts by type/source, time span,
      high-confidence count, top facts by q_value+recency, L0 digest).
      Exposed as `memtether profile` and MCP tool `profile` (9 → 10 tools).
      Zero LLM calls by design: same DB → same profile, every line traceable.
- [x] Hub test suite from 0 → 125 (gate / hub_selfcheck / is_junk / NREM
      consolidation / trigger_detector / preflight / mem CLI / DB-path guards)
- [x] `cluster_semantic()` empty-input return type unified (was `[]` vs a tuple)
- [x] `governance` no longer crashes with "no such table: facts" on a fresh DB
- [x] `governance.DB` honours `MEM_DB` (was hardcoded → two DBs in one session)
- [x] Entity index column fix (`entity_name` → `entity`); the facts half of the
      index had been silently dead behind a swallowed OperationalError
- [x] `mem.py` `sanitize()` now masks ghp_/AKIA/glpat-/xoxb- (was sk- only)
- [x] Blank `--source` no longer errors out (falls back to DEFAULT_SOURCE)
- [x] `MANIFEST.in` excludes `*.bak*` — a NOTICE backup had shipped in the a82
      wheel; packaging now matches what `.gitignore` already enforced

## Completed (a81)

- [x] MCP governance tools (correct/retire/pin/as_of/timeline) — 33b73d7
- [x] recipes write API + stable-uid upsert + 7 seeded recipes — d583667
- [x] Predicate extraction quality rebuild (prose-junk 56%→0%) — 5d45504
- [x] Smithery login + MCP directories (Registry/Glama/Smithery/DevHunt/Uneed/LibHunt)
- [x] Dynamic rebuild quota — 15741e9
- [x] Governance tests 11→27, proj_judge 96/100 — c2dff94

## Mid-term

- [ ] Required-and-registered `source` at write time. The junk gate's reliable
      signal is the source blacklist, but a blank source with bare "probe"
      wording is still undetectable without re-introducing false hits
      (measured: 35/300 on real production rows when the word list was broad).
      Enforcing source at the write entry point closes that hole properly.
- [ ] Role-based access control on the REST API (currently single-key)
- [ ] Migration adapters: import from mem0 / zep / letta memory stores
- [ ] Post-processing benchmark type (MemDaily alignment)
- [ ] Trajectory→memory compilation pipeline (LongMemEval-V2 alignment)

## Explicitly out of scope

- Cloud / hosted deployment (conflicts with the local-first design goal)
- Full RBAC (single-user tool; multi-user trust is operational, not code)
- Training-time defenses (MemTether secures operation-time memory only)

## Non-goals that will not change

- **No deletion.** Corrections and retirements mark rows; content is
  preserved for audit. This is the core design principle.
- **No silent filtering.** Suspicious memories stay retrievable; governance
  is explicit and auditable.