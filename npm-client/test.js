'use strict';
/**
 * Self-test: requires a running MemTether server (memtether dashboard).
 * Uses the demo DB if MEM_DB points at one — never touches production data.
 * Usage: node test.js [baseUrl]
 */
const { MemTetherClient } = require('./index.js');

async function main() {
  const m = new MemTetherClient({ baseUrl: process.argv[2] || 'http://127.0.0.1:8820' });
  const h = await m.health();
  console.log('health:', JSON.stringify(h).slice(0, 120));
  const { uid } = await m.remember('npm-client smoke test ' + Date.now(), { source: 'npm-test' });
  if (!uid) throw new Error('remember returned no uid');
  console.log('remember: PASS uid=' + uid);
  const s = await m.search('npm-client smoke test', { limit: 3 });
  const hit = (s.results || []).some(r => r.uid === uid);
  console.log('search: ' + (hit ? 'PASS (found uid)' : 'WARN (uid not in top hits)'));
  const cv = await m.courtVerify();
  console.log('courtVerify:', JSON.stringify(cv).slice(0, 160));
  console.log('ALL PASS');
}

main().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
