# @memtether/client

Thin REST client for [MemTether](https://github.com/MemTether/MemTether) — the
governed AI agent memory with a tamper-evident audit chain.

Zero dependencies. Plain fetch. Every fact carries a mandatory source;
corrections supersede (never delete); the audit log is hash-chained.

## Start a server

```bash
pip install memtether
memtether dashboard        # http://127.0.0.1:8820
```

## Use

```js
const { MemTetherClient } = require('@memtether/client');
const m = new MemTetherClient({ baseUrl: 'http://127.0.0.1:8820' });

await m.health();
const { uid } = await m.remember('User prefers dark mode', { source: 'my-app' });
const { results } = await m.search('dark mode', { limit: 3 });
await m.correct(uid, 'User prefers light mode now', { reason: 'changed' });
const verdict = await m.courtVerify();   // hash-chain check
```

| Method | Endpoint | Notes |
|---|---|---|
| `health()` | GET /health | liveness + version |
| `stats()` | GET /stats | counts / type distribution |
| `remember(content, opts)` | POST /remember | returns the audit-traceable `uid` |
| `search(query, opts)` | POST /search | hybrid keyword+vector, RRF fusion |
| `correct(uid, content, opts)` | POST /correct | supersedes, never deletes |
| `retire(uid, opts)` | POST /retire | marked superseded; content preserved |
| `list(opts)` | GET /list | recent memories |
| `timeline(uid)` | GET /timeline/{uid} | full provenance chain |
| `courtVerify()` | GET /court/verify | hash-chain check |
| `court(uid)` | GET /court/{uid} | one fact's dossier |

Apache-2.0 — same as MemTether.
