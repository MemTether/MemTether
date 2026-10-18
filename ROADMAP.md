# Roadmap

MemTether is under active development. This document tracks what is
planned, in progress, and deliberately out of scope.

## Near-term (next minor release)

- [x] External hash-chain anchoring (c0bb0b5 — `court --anchor-external` / `--verify-external`)
- [ ] Auto-anchor performance benchmark (currently unmeasured)
- [x] SGM 2-hop relation queries (19e97d0 — bridge-entity expansion with term-quality filter)
- [x] Predicate extraction quality rebuild (5d45504 — prose-junk 56%→0%, multi-word entities fixed)

## Completed (a81)

- [x] MCP governance tools (correct/retire/pin/as_of/timeline) — 33b73d7
- [x] recipes write API + stable-uid upsert + 7 seeded recipes — d583667
- [x] Predicate extraction quality rebuild (prose-junk 56%→0%) — 5d45504
- [x] Smithery login + MCP directories (Registry/Glama/Smithery/DevHunt/Uneed/LibHunt)
- [x] Dynamic rebuild quota — 15741e9
- [x] Governance tests 11→27, proj_judge 96/100 — c2dff94

## Mid-term

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