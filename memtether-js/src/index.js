
'use strict';
const { spawnSync } = require('child_process');
const path = require('path');

function _run(args) {
  const result = spawnSync('memtether', args, { encoding: 'utf-8', timeout: 30000 });
  if (result.error) throw result.error;
  return result.stdout;
}

function remember(content, options = {}) {
  const args = ['remember', content, '--source', options.source || 'js-sdk', '--type', options.type || 'fact'];
  const out = _run(args);
  const match = out.match(/uid[=:\s]+([\w-]+)/);
  return { ok: out.includes('ÒÑÐ´Èë') || out.includes('written'), uid: match ? match[1] : null, raw: out };
}

function search(query, limit = 10) {
  const out = _run(['search', query, '--list', '--limit', String(limit)]);
  const lines = out.split('\n').filter(l => l.trim().match(/^\d+\./));
  return lines.map(l => {
    const uidMatch = l.match(/uid=([\w-]+)/);
    const scoreMatch = l.match(/([\d.]+)$/);
    return { line: l.trim(), uid: uidMatch ? uidMatch[1] : null, score: scoreMatch ? parseFloat(scoreMatch[1]) : null };
  });
}

function stats() {
  const out = _run(['stats']);
  const stats = {};
  out.split('\n').forEach(l => {
    const m = l.match(/^(\w+)\s+(\d+)/);
    if (m) stats[m[1]] = parseInt(m[2]);
  });
  return stats;
}

module.exports = { remember, search, stats, _run };
