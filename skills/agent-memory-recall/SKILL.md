---
name: agent-memory-recall
description: Search-before-answer protocol for MemTether. Use when your agent is about to answer from memory, state a fact about the user/project, or repeat work from a previous session — check L0 recall first so answers cite governed, sourced memory instead of guesswork.
---

# MemTether Recall Skill

Check governed memory **before** answering. MemTether memories carry a mandatory
source, a hash-chained audit trail, and bi-temporal timestamps, so a recall hit is
traceable — a memory-only answer is not.

## The protocol

1. **Before answering anything that smells like "what do we know about X"**, run:

   ```bash
   memtether search "X" --list --limit 5
   ```

   `--list` returns one-line summaries (L0) with UIDs and scores. This costs ~10% of
   the characters of full results and is enough to decide whether memory has anything
   relevant.

2. **Only if a hit looks relevant**, fetch the full record:

   ```bash
   memtether provenance <uid>
   ```

   This shows who wrote it, when it took effect, what it superseded, and whether the
   hash chain verified.

3. **Answer with the citation**: state the fact and its source/uid. If the hit is
   `superseded`, follow the chain to the active version instead of quoting the stale one.

## When NOT to search

- The question is about code currently open in the editor — read the file, not memory.
- The user is explicitly asking for a fresh web lookup.
- The query is a follow-up referring to something already in the current conversation.

## After learning something new

If the conversation established a durable fact/decision/preference, write it with an
explicit source so recall can attribute it later:

```bash
memtether remember "Deployment target moved to staging cluster" --source claude-code
```

Then verify the write landed governed (not just echoed):

```bash
memtether court --verify
```
