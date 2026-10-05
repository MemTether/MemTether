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

app = FastAPI(title="MemTether API", description="Cross-client AI memory hub", version="0.1.0a19")

from fastapi.middleware.cors import CORSMiddleware

# P2 (2026-10-05): rate limiting — 60 req/min default (via slowapi if available)
class _NoopLimiter:
    def limit(self, *a, **k):
        def deco(f):
            return f
        return deco

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
    app.state.limiter = _limiter
    app.add_exception_handler(429, _rate_limit_exceeded_handler)
except ImportError:
    _limiter = _NoopLimiter()
_API_KEY_ENV = os.environ.get("MEMTETHER_API_KEY", "").strip()
# P7 (2026-10-05): wildcard CORS is fine for localhost-only use, but a keyed
# deployment should not accept authenticated requests from any origin.
_ALLOW_ORIGINS = ["*"] if not _API_KEY_ENV else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOW_ORIGINS,
    allow_credentials=bool(_ALLOW_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

# P0-5 (2026-10-05): optional API key auth - enforced only when MEMTETHER_API_KEY is set.
# Docs previously claimed this existed; it did not. Now it does.
from fastapi import Request as _Req
_API_KEY = os.environ.get("MEMTETHER_API_KEY", "").strip()

if _API_KEY:
    from starlette.middleware.base import BaseHTTPMiddleware
    class _ApiKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path in ('/health', '/', '/dashboard'):
                return await call_next(request)
            if request.headers.get('authorization', '') != 'Bearer ' + _API_KEY:
                from fastapi.responses import JSONResponse
                return JSONResponse({'ok': False, 'error': 'unauthorized'}, status_code=401)
            return await call_next(request)
    app.add_middleware(_ApiKeyMiddleware)


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

class AbsorbRequest(BaseModel):
    content: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    type: str = Field(default="fact")
    dry_run: bool = Field(default=True)

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
    from importlib.metadata import version as _pkgver
    try:
        _v = _pkgver("memtether")
    except Exception:
        _v = "unknown"
    return {"ok": True, "version": _v}

@app.post("/remember")
@_limiter.limit("30/minute")
async def remember(req: RememberRequest):
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _remember_sync, req)


def _remember_sync(req):
    return gateway.remember(content=req.content, type=req.type, source=req.source, tags=req.tags, confidence=req.confidence)

@app.post("/search")
@_limiter.limit("120/minute")
async def search(req: SearchRequest):
    """P1 (2026-10-05): async wrapper - runs gateway.search in thread pool
    so the event loop is not blocked by FTS5/vector computation. Compatible
    with async agent frameworks (LangGraph, Flowise, n8n)."""
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _search_sync, req.query, req.limit)
    return result


def _search_sync(query, limit):
    try:
        result = gateway.search(query, limit)
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
@_limiter.limit("30/minute")
def correct(req: CorrectRequest):
    return gateway.correct(req.old_uid, req.new_content, req.reason, source=req.source)

@app.post("/retire")
@_limiter.limit("30/minute")
def retire(req: RetireRequest):
    return gateway.retire(req.uid, req.reason, by_agent=req.source)

@app.get("/list")
def list_memories(limit: int = 20):
    gateway.init_db()
    conn = gateway.get_conn()
    try:
        rows = conn.execute("SELECT uid, type, content, source, updated_at FROM facts WHERE status='active' AND scope NOT IN ('private','restricted') ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return {"ok": True, "count": len(rows), "items": [dict(r) for r in rows]}
    finally:
        conn.close()

@app.get("/timeline/{uid}")
def timeline_ep(uid: str):
    """Supersession chain for a fact: forward evolution (who replaced it)."""
    try:
        return {"ok": True, "uid": uid, "chain": gateway.timeline(uid)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:300])



# P2-5 (2026-10-05): reuse governance polarity lexicons for absorb.
try:
    import governance as _gov
    _NEG_LEX = _gov._NEG
    _POS_LEX = _gov._POS
except Exception:  # governance optional at API layer
    _NEG_LEX, _POS_LEX = (), ()


def _polarity(text):
    t = (text or '').lower()
    n = sum(1 for w in _NEG_LEX if w.lower() in t)
    p = sum(1 for w in _POS_LEX if w.lower() in t)
    return p - n  # <0 means negative-dominated

@app.post("/absorb")
def absorb(req: AbsorbRequest):
    """Keyword-based absorb: classify incoming fact against existing memories.

    Returns classification per candidate (duplicate / contradiction / related / new)
    and optionally writes if dry_run=False. Uses gateway.remember + conflict detection.
    """
    import sqlite3
    db_path = os.environ.get("MEM_DB", "memory.db")
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # find top candidates by simple keyword overlap (L0 absorb, no LLM)
        words = set(req.content.lower().split())
        rows = conn.execute(
            "SELECT uid, content, type FROM facts WHERE status='active' AND scope NOT IN ('private','restricted') ORDER BY updated_at DESC LIMIT 200"
        ).fetchall()
        candidates = []
        for r in rows:
            rwords = set((r['content'] or '').lower().split())
            overlap = len(words & rwords) / max(len(words | rwords), 1)
            if overlap > 0.15:
                candidates.append({'uid': r['uid'], 'content': r['content'][:120],
                                   'type': r['type'], 'overlap': round(overlap, 3)})
        candidates.sort(key=lambda x: x['overlap'], reverse=True)
        conn.close()

        # classify best candidate
        classification = 'new'
        if candidates:
            top = candidates[0]['overlap']
            if top > 0.7:
                classification = 'duplicate'
            elif top > 0.4:
                # check polarity for contradiction
                neg_in = _polarity(req.content)
                neg_ex = _polarity(candidates[0]['content'])
                classification = 'contradiction' if ((neg_in < 0) != (neg_ex < 0)) else 'update'
            elif top > 0.15:
                classification = 'related'

        result = {
            'ok': True,
            'classification': classification,
            'candidates': candidates[:5],
            'dry_run': req.dry_run,
        }
        if not req.dry_run and classification in ('new', 'update'):
            wr = gateway.remember(content=req.content, type=req.type, source=req.source)
            result['write'] = wr
        elif not req.dry_run and classification == 'duplicate':
            result['write'] = {'skipped': True, 'reason': 'duplicate of existing fact'}
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:300])

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
