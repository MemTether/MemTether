# Roadmap

MemTether is under active development. This document tracks what is
planned, in progress, and deliberately out of scope.

## Near-term (next minor release)

- [ ] External hash-chain anchoring (append-only file or RFC-3161 TSA)
      to close the same-database anchor limitation
- [ ] Auto-anchor performance benchmark (currently unmeasured)
- [ ] SGM 2-hop relation queries (`A 的学校 的 城市` style chains)
- [ ] English predicate extraction (currently Chinese `的`-pattern only)

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