---
name: agent-memory-court
description: Write-then-verify protocol for MemTether. Use after any memory write, correction, or retirement — verify the hash chain, walk the provenance timeline, and confirm the supersession landed so agents can prove what they know instead of assuming it.
---

# MemTether Court Skill

A memory write that cannot be proven is a rumor. After your agent writes,
corrects, or retires anything in MemTether, close the loop:

## 1. Verify the hash chain

```bash
memtether court --verify
```

`PASS` means every audit entry is still cryptographically linked. `FAIL` means
the database was modified outside MemTether — stop and investigate, do not keep
writing on top of a broken chain.

## 2. Inspect the fact you just touched

```bash
memtether court <uid>
memtether timeline <uid>
```

The case file shows the bi-temporal window (when it was true vs when the system
learned it), the provenance chain (which agent wrote it, which agent corrected
it), and the integrity proof. The timeline walks supersession links forward.

## 3. Confirm supersession semantics

After a `correct`, the old fact is **superseded, not deleted**:

```bash
memtether search "old phrasing" --list
# old uid shows as superseded with a pointer to the new one
```

If both old and new show `active`, something went wrong — surface it to the user
instead of continuing.

## 4. Adjudicate conflicts honestly

If `memtether conflicts --stats` shows pending candidates, do not auto-resolve
contradictions between different sources. Surface them:

```bash
memtether conflicts --stats
```

MemTether's conflict gate is deliberately human-in-the-loop. Agents propose,
humans decide — that decision is then part of the audited chain.

## Why this matters

Every MemTether write is appended to a SHA-256 hash chain with mandatory source
attribution. Running this protocol after writes means any later question of
"who told you that?" has a cryptographic answer, and tampering (accidental or
malicious) is detectable within seconds.
