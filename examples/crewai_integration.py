"""
CrewAI + MemTether: governed shared memory for multi-agent crews.

CrewAI's built-in memory doesn't have audit trails or conflict gating.
MemTether adds: source attribution, hash-chained audit log, bi-temporal
supersession, and conflict detection — all via a simple Python API.

Usage: pip install crewai memtether
"""

from crewai import Agent, Task, Crew
import memtether
from gateway import correct, as_of

# Initialize MemTether (uses local SQLite, no cloud)
import os, tempfile
os.environ["MEM_DB"] = os.path.join(tempfile.mkdtemp(), "crew_memory.db")
os.environ.setdefault("MEMTETHER_HOME", os.environ["MEM_DB"].rsplit("/",1)[0])

# Researcher writes findings to shared memory
researcher = Agent(
    role="Senior Researcher",
    goal="Find and verify facts about {topic}",
    backstory="You research topics and record verified findings.",
    allow_delegation=False,
)

# Writer reads from shared memory
writer = Agent(
    role="Writer",
    goal="Write a report based on verified research",
    backstory="You write reports using verified facts from the research phase.",
    allow_delegation=False,
)

# Simulate: researcher records a finding
finding = "LangChain 0.3 supports Python 3.12 natively"
r = memtether.remember(finding, type="fact", source="researcher")
print(f"[Researcher] recorded: {finding} (uid={r['uid']})")

# Simulate: a correction comes in (the version was wrong)
from gateway import correct as mt_correct
r2 = mt_correct(r["uid"], "LangChain 0.3 supports Python 3.13 natively",
                reason="version check: actual is 3.13", by_agent="reviewer")
print(f"[Reviewer] corrected: {r2['new_uid']} (old {r['uid']} superseded)")

# The old finding is NOT deleted — it's superseded
from gateway import as_of
history = as_of("2026-01-01", kind="valid")
print(f"[Audit] facts valid before 2026: {history['count']} (old value preserved)")

# Query current state
results = memtether.search("LangChain Python", limit=3)
for hit in results.get("results", []):
    print(f"[Search] {hit['content'][:60]} (score={hit.get('score','?')})")

# Every operation is hash-chained — run court to verify integrity
import subprocess
court = subprocess.run(["memtether", "court", r2["new_uid"]],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
print("\n" + court.stdout)
