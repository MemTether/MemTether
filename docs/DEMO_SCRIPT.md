# 3-Minute Demo Script

## Setup (before demo)
```bash
pip install memtether
```

## 0:00 — Install + init (30s)
```bash
memtether init
```
*"One command. Detects 23 AI clients, writes MCP config, creates the memory DB."*

## 0:30 — Write from two different agents (30s)
```bash
# Agent 1 (Claude Code) writes a fact
memtether remember "Deploy to port 8080" --source claude-code

# Agent 2 (Cursor) reads it
memtether search "deploy port"
```
*"Different agents, same memory. No sync daemon."*

## 1:00 — Correct a fact (30s)
```bash
memtether correct fact-xxx "Deploy to port 9090" --reason "port changed"
memtether timeline fact-xxx
```
*"The old value isn't deleted — it's superseded. The chain is traceable."*

## 1:30 — Conflict detection (30s)
```bash
memtether conflicts --list
memtether conflicts --accept 1 --by demo
```
*"Governance in action: candidates → human review → resolved."*

## 2:00 — Semantic search with local models (30s)
```bash
memtether download-models --profile bge-small-zh
python -m memsearch --rebuild
memtether search "deployment configuration" --semantic
```
*"Local embedding, no cloud, no API fees."*

## 2:30 — Verify everything (30s)
```bash
python scripts/make_jury_report.py
cat VERIFICATION_REPORT.txt
```
*"Every claim has a command. Every number has an artifact."*
