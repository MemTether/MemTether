"""
LangChain Memory class backed by MemTether.

Drop this into any LangChain agent as a memory backend:

    from langchain_memory_memtether import MemTetherMemory
    
    memory = MemTetherMemory()
    # or with a specific DB path:
    memory = MemTetherMemory(db_path="/path/to/memory.db")

Requires: pip install memtether langchain-core
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

try:
    from langchain_core.memory import BaseMemory
except ImportError:
    try:
        from langchain.schema import BaseMemory
    except ImportError:
        # Fallback minimal base class for standalone testing
        class BaseMemory(BaseModel):
            class Config:
                arbitrary_types_allowed = True
            @property
            def memory_variables(self) -> List[str]:
                return []
            def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
                return {}
            def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, str]) -> None:
                pass
            def clear(self) -> None:
                pass

import sys
import os

# Ensure memtether is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))) if not os.environ.get("MEMTETHER_INSTALLED") else None


class MemTetherMemory(BaseMemory):
    """LangChain memory backend backed by MemTether's governed SQLite store.
    
    Features:
    - Hash-chained audit log (tamper-evident)
    - Source attribution on every write
    - Bi-temporal supersession (corrections never delete)
    - Conflict gate for contradictory facts
    """
    
    source: str = Field(default="langchain-agent", description="Source tag for this agent")
    search_limit: int = Field(default=5, description="Max memories to recall per query")
    db_path: Optional[str] = Field(default=None, description="Optional: path to SQLite DB file")
    
    class Config:
        arbitrary_types_allowed = True
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._setup_env()
        import memtether
        self._mt = memtether
        # Import gateway for correct/as_of
        import gateway
        self._gw = gateway
    
    def _setup_env(self):
        """Set up environment for MemTether if db_path is specified."""
        if self.db_path:
            import pathlib
            db = pathlib.Path(self.db_path)
            db.parent.mkdir(parents=True, exist_ok=True)
            os.environ["MEM_DB"] = str(db)
    
    @property
    def memory_variables(self) -> List[str]:
        """Variables this memory provides to the chain."""
        return ["history", "context"]
    
    def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Recall relevant memories based on the current input."""
        query = ""
        for v in inputs.values():
            if isinstance(v, str) and len(v) > len(query):
                query = v
        
        if not query:
            return {"history": "", "context": ""}
        
        try:
            results = self._mt.search(query, limit=self.search_limit)
            hits = results.get("results", [])
            
            history_lines = []
            context_lines = []
            for h in hits:
                content = h.get("content", "")
                source = h.get("source", "unknown")
                score = h.get("score", "?")
                status = h.get("status", "active")
                if status == "active":
                    history_lines.append(f"[{source}] {content}")
                    context_lines.append(f"- {content} (source: {source})")
            
            return {
                "history": "\n".join(history_lines[-3:]),
                "context": "\n".join(context_lines),
            }
        except Exception:
            return {"history": "", "context": ""}
    
    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, str]) -> None:
        """Save the current interaction to MemTether with source attribution."""
        # Extract the key content from inputs and outputs
        input_text = ""
        output_text = ""
        for v in inputs.values():
            if isinstance(v, str) and len(v) > len(input_text):
                input_text = v
        for v in outputs.values():
            if isinstance(v, str) and len(v) > len(output_text):
                output_text = v
        
        # Store the output (agent's response) as a memory
        if output_text.strip():
            try:
                self._mt.remember(output_text[:500], type="fact", source=self.source)
            except Exception:
                pass  # Don't fail the chain if memory write fails
    
    def correct_memory(self, uid: str, new_content: str, reason: str = "corrected") -> Optional[str]:
        """Correct an existing memory (supersede, never delete)."""
        try:
            r = self._gw.correct(uid, new_content, reason=reason, by_agent=self.source)
            return r.get("new_uid") if r.get("ok") else None
        except Exception:
            return None
    
    def query_as_of(self, timestamp: str, kind: str = "valid") -> dict:
        """Query what the system believed at a past point (bi-temporal)."""
        try:
            return self._gw.as_of(timestamp, kind=kind)
        except Exception:
            return {}
    
    def clear(self) -> None:
        """Clear is a no-op — MemTether never deletes, it supersedes."""
        pass  # Intentionally do nothing: MemTether's design principle is "never delete"
