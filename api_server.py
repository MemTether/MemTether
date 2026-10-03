# -*- coding: utf-8 -*-
"""api_server.py — REST API Server for MemTether (S1)"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import memtether_paths as _mp
    _DB = _mp.default_db()
except Exception:
    _DB = os.environ.get("MEM_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.db"))
os.environ.setdefault("MEM_DB", _DB)

import gateway
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="MemTether API", description="Cross-client AI memory hub", version="0.1.0a9")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RememberRequest(BaseModel):
    content: str = Field(..., min_length=1)
    type: str = Field(default="fact")
    source: str = Field(..., min_length=1)
    tags: str = Field(default="")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=100)

class CorrectRequest(BaseModel):
    old_uid: str
    new_content: str = Field(..., min_length=1)
    reason: str = Field(default="")
    source: str

class RetireRequest(BaseModel):
    uid: str
    reason: str = Field(default="")
    source: str

class QValueRequest(BaseModel):
    uid: str
    reward: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str



from fastapi.responses import HTMLResponse
import os



@app.get("/", response_class=HTMLResponse)
def root():
    """Redirect to dashboard."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Serve the web dashboard."""
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, encoding="utf-8") as f:
            return f.read()
    return "<h1>Dashboard not found</h1><p>Make sure dashboard.html is in the same directory as api_server.py</p>"

@app.get("/health")
def health():
    return {"ok": True, "version": "0.1.0a9"}

@app.post("/remember")
def remember(req: RememberRequest):
    return gateway.remember(content=req.content, type=req.type, source=req.source, tags=req.tags, confidence=req.confidence)

@app.post("/search")
def search(req: SearchRequest):
    try:
        result = gateway.search(req.query, req.limit)
        # A1: Auto-increment use_count for returned facts (passive tracking)
        if isinstance(result, dict) and "results" in result:
            import sqlite3
            db_path = os.environ.get("MEM_DB", "memory.db")
            try:
                conn = sqlite3.connect(db_path)
                for r in result["results"]:
                    uid = r.get("uid", "")
                    if uid:
                        conn.execute("UPDATE facts SET use_count = use_count + 1 WHERE uid = ?", (uid,))
                conn.commit()
                conn.close()
            except Exception:
                pass  # don't fail the search if bump fails
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:300])

@app.get("/stats")
def stats():
    return gateway.stats()

@app.post("/correct")
def correct(req: CorrectRequest):
    return gateway.correct(req.old_uid, req.new_content, req.reason, source=req.source)

@app.post("/retire")
def retire(req: RetireRequest):
    return gateway.retire(req.uid, req.reason, by_agent=req.source)

@app.get("/list")
def list_memories(limit: int = 20):
    gateway.init_db()
    conn = gateway.get_conn()
    try:
        rows = conn.execute("SELECT uid, type, content, source, updated_at FROM facts WHERE status='active' ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return {"ok": True, "count": len(rows), "items": [dict(r) for r in rows]}
    finally:
        conn.close()

@app.post("/qvalue")
def qvalue(req: QValueRequest):
    return gateway.bump_qvalue(req.uid, req.reward, req.source)

if __name__ == "__main__":
    import argparse, uvicorn
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    a = p.parse_args()
    uvicorn.run(app, host=a.host, port=a.port)
