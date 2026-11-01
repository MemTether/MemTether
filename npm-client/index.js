'use strict';
/**
 * @memtether/client — thin REST wrapper for a MemTether server.
 *
 * MemTether is a governed memory hub: every fact carries a mandatory source,
 * corrections supersede (never delete), and the audit log is hash-chained.
 * This client is deliberately tiny — plain fetch, zero dependencies.
 *
 * Start a server:  memtether dashboard   (or: uvicorn api_server:app --port 8820)
 * Docs:            https://github.com/MemTether/MemTether
 */

const DEFAULT_BASE = 'http://127.0.0.1:8820';

class MemTetherClient {
  /**
   * @param {object} [opts]
   * @param {string} [opts.baseUrl]  default http://127.0.0.1:8820
   * @param {string} [opts.apiKey]   sent as Authorization: Bearer <key> if set
   * @param {number} [opts.timeoutMs] request timeout, default 10000
   */
  constructor(opts = {}) {
    this.baseUrl = (opts.baseUrl || DEFAULT_BASE).replace(/\/+$/, '');
    this.apiKey = opts.apiKey || '';
    this.timeoutMs = opts.timeoutMs || 10000;
  }

  /** @private */
  async _req(method, path, body) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.timeoutMs);
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (this.apiKey) headers['Authorization'] = `Bearer ${this.apiKey}`;
      const res = await fetch(this.baseUrl + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
      });
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : null; } catch { data = text; }
      if (!res.ok) {
        const err = new Error(`MemTether ${method} ${path} -> ${res.status}: ${typeof data === 'string' ? data : JSON.stringify(data)}`);
        err.status = res.status;
        err.data = data;
        throw err;
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  /** Liveness + version. GET /health */
  health() { return this._req('GET', '/health'); }

  /** Store counts / type distribution. GET /stats */
  stats() { return this._req('GET', '/stats'); }

  /**
   * Write a governed memory. POST /remember
   * @param {string} content  the fact
   * @param {object} [opts]   { type='fact', source='npm-client', tenant }
   * @returns {{uid: string, ...}} includes the audit-traceable uid
   */
  remember(content, opts = {}) {
    return this._req('POST', '/remember', {
      content,
      type: opts.type || 'fact',
      source: opts.source || 'npm-client',
      tenant: opts.tenant,
    });
  }

  /**
   * Hybrid search (keyword + vector + literal, RRF fusion). POST /search
   * @param {string} query
   * @param {object} [opts] { limit=10, tenant, scope }
   */
  search(query, opts = {}) {
    return this._req('POST', '/search', {
      query, limit: opts.limit || 10, tenant: opts.tenant, scope: opts.scope,
    });
  }

  /** Correct a memory: supersedes the old fact (never deletes). POST /correct */
  correct(uid, content, opts = {}) {
    return this._req('POST', '/correct', { uid, content, reason: opts.reason || '', by: opts.by || 'npm-client' });
  }

  /** Retire a memory (marked superseded; content preserved). POST /retire */
  retire(uid, opts = {}) {
    return this._req('POST', '/retire', { uid, reason: opts.reason || '', by: opts.by || 'npm-client', apply: true });
  }

  /** List memories. GET /list?limit=&type= */
  list(opts = {}) {
    const q = new URLSearchParams();
    if (opts.limit) q.set('limit', String(opts.limit));
    if (opts.type) q.set('type', opts.type);
    const qs = q.toString();
    return this._req('GET', '/list' + (qs ? '?' + qs : ''));
  }

  /** Full provenance for one fact: supersession chain + source + timestamps. GET /timeline/{uid} */
  timeline(uid) { return this._req('GET', `/timeline/${encodeURIComponent(uid)}`); }

  /** Hash-chain verification. GET /court/verify */
  courtVerify() { return this._req('GET', '/court/verify'); }

  /** One fact's court dossier (temporal + provenance + integrity). GET /court/{uid} */
  court(uid) { return this._req('GET', `/court/${encodeURIComponent(uid)}`); }
}

module.exports = { MemTetherClient, DEFAULT_BASE };
