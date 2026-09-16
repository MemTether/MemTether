# -*- coding: utf-8 -*-
"""
gateway.py — Memory Gateway（唯一记忆入口，astra 终版架构 2026-09-13）

定位：三个 agent（WorkBuddy/DeepSeek、OpenClaw/grok🦞、豆包）统一读写记忆的唯一入口。
分层（astra 裁决）：
  - SQLite memory.db  = 权威事实账本（唯一真源）
  - Mem0              = 自动提取 + 冲突消解 + 语义检索引擎
  - sink.json         = 兼容导出（不再是运行时真源）
  - *.md / projections = 人读投影（由本层自动生成，勿手改）

核心原则：
  1. 任何 agent 不得直接改 sink.json / memory.db / Mem0 存储，一律走本 gateway。
  2. 每条事实必须有 source / ts / status / confidence。
  3. 新事实覆盖旧事实时建立 supersession 链，旧事实不物理删除。
  4. 工具/路径/地址进结构化资产表，不做纯散文记忆。
  5. 任务检索优先返回「配方」，不是散乱记忆。

子命令（统一入口）：
  remember     写入/更新一条事实（自动 ADD/UPDATE/supersede）
  search       混合检索（关键词 + 语义 + 工具别名 + 状态加权）
  get          精确取一条
  correct      用户纠正：立即 supersede 旧事实
  retire       退役机制/工具
  record_tool  记录工具/路径/地址资产
  record_incident  记录故障与修复
  resolve_task 按任务名返回完整执行配方（工具+路径+命令+坑+备用）
  rebuild      从 SQLite 重建所有投影（sink.json + md + MEMORY.md）
  stats        统计
  migrate      从旧 sink.json 迁移（阶段3 用）

用法：
  python gateway.py <子命令> [参数]
"""
import os
import sys
import json
import time
import sqlite3
import hashlib
import argparse
from pathlib import Path

HUB = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HUB, 'memory.db')
SINK = os.path.join(HUB, 'sink.json')

# ---- SQLite schema ----
SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE,            -- 稳定唯一 id
  type TEXT,                  -- fact/decision/incident/preference/environment/tool/path
  subject TEXT,               -- 主语（如 user/system/tool:<name>）
  content TEXT NOT NULL,      -- 正文
  status TEXT DEFAULT 'active', -- candidate/active/superseded/revoked/retired/conflicted/frozen
  superseded_by TEXT,         -- 指向替代它的 uid
  valid_from TEXT,            -- T轴(有效时间): 现实世界开始成立的时间
  valid_to TEXT,              -- T轴: 现实世界停止成立的时间(NULL=仍成立)
  recorded_at TEXT,           -- T'轴(摄录时间): 系统第一次记录该事实的时刻
  invalidated_at TEXT,        -- T'轴: 系统第一次认定该事实失效的时刻
  temporal_source TEXT,       -- 时间轴数据来历 native/backfilled/inferred
  source TEXT,                -- workbuddy/openclaw/doubao_a/doubao_b/user/legacy
  scope TEXT DEFAULT 'shared',-- shared/workbuddy/openclaw/doubao/project:<n>/run:<id>
  confidence REAL DEFAULT 0.8,
  tags TEXT,                  -- 逗号分隔
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS tool_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE,
  name TEXT,                  -- 工具名
  aliases TEXT,               -- 别名（逗号分隔）
  type TEXT,                  -- local_tool/remote_service/endpoint/cli/script/folder
  status TEXT DEFAULT 'active',
  path TEXT,                  -- 主路径
  entrypoint TEXT,            -- 启动命令/URL
  capabilities TEXT,          -- 能力（逗号分隔）
  recipe_ids TEXT,            -- 关联配方 uid
  known_failures TEXT,        -- 已知坑（JSON）
  prerequisites TEXT,         -- 前置条件
  last_verified_at TEXT,
  verification_method TEXT,
  source TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE,
  task_patterns TEXT,         -- 任务触发词（逗号分隔）
  preferred_tool TEXT,        -- 首选工具 uid
  steps TEXT,                 -- 步骤（JSON 数组）
  output_paths TEXT,          -- 产物路径（JSON）
  known_pitfalls TEXT,        -- 坑（JSON）
  fallback_tools TEXT,        -- 备用工具 uid
  last_success_at TEXT,
  source TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS supersessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  old_uid TEXT,
  new_uid TEXT,
  reason TEXT,
  by_agent TEXT,
  ts TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  op TEXT,
  target TEXT,
  agent TEXT,
  detail TEXT,
  ts TEXT
);

-- 冲突复核留痕（2026-09-16）
-- 动机：detect_explicit_conflicts 被定位为"误报率低的确定性检测"，但实测仍会误判——
--   例：「四层架构已落地(含 astra 通道)」vs「astra 403 欠费」被判为极性冲突，
--   实为**互补信息**（一个讲配置存在、一个讲当前故障），并不矛盾。
--   若无复核留痕，这类误报会永久扣治理度分且无人能纠正。
-- 设计：规则只负责**生成候选**，人工复核结论单独留痕；评分卡据此排除已否定的对。
CREATE TABLE IF NOT EXISTS conflict_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid_a TEXT,                 -- 无序对，写入时按 uid 字典序归一
  uid_b TEXT,
  verdict TEXT,               -- no_conflict / real_conflict
  note TEXT,
  by_agent TEXT,
  ts TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT,
  type TEXT,
  reason TEXT,
  source TEXT,
  ts TEXT,
  status TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  event_type TEXT NOT NULL,        -- run_finished / incident / retrieval_miss
  agent TEXT,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending / processed / failed / skipped
  retry_count INTEGER DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  processed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_run_event
  ON run_events(run_id, event_type);
"""

STATUSES = {'candidate', 'active', 'superseded', 'revoked', 'retired', 'conflicted', 'frozen'}


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _uid(prefix, text):
    h = hashlib.md5(text.encode('utf-8')).hexdigest()[:12]
    return '%s-%s-%s' % (prefix, time.strftime('%Y%m%d%H%M%S'), h)


def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def audit(conn, op, target, agent, detail=''):
    conn.execute('INSERT INTO audit_log (op,target,agent,detail,ts) VALUES (?,?,?,?,?)',
                 (op, target, agent, detail[:500], now()))


def _mem0_add(content, user_id='wzj'):
    """可选：同步写入 Mem0（自动提取+冲突消解）。失败不阻塞主流程。"""
    try:
        sys.path.insert(0, HUB)
        from mem0 import Memory
        import mem0_config
        m = Memory.from_config(mem0_config.MEM0_CONFIG)
        r = m.add(content, user_id=user_id)
        return r
    except Exception as e:
        return {'error': str(e)[:120]}


def _vec_upsert(uid, content, type_='fact', source='workbuddy'):
    """把事实同步进 ChromaDB facts_active 向量索引（2026-09-13 自动同步）。失败不阻塞。"""
    try:
        sys.path.insert(0, HUB)
        import memsearch
        if memsearch.is_generic_garbage(content):
            return {'skipped': 'generic_garbage'}
        col = memsearch._client().get_or_create_collection(
            memsearch.COLLECTION, metadata={'hnsw:space': 'cosine'})
        vec = memsearch._embed([content])[0]
        col.upsert(ids=[uid], embeddings=[vec], documents=[content],
                   metadatas=[{'uid': uid, 'type': type_, 'source': source, 'kind': 'fact'}])
        return {'ok': True}
    except Exception as e:
        return {'error': str(e)[:120]}


def _vec_delete(uid):
    """从向量索引删除（supersede/retire 时）。失败不阻塞。"""
    try:
        sys.path.insert(0, HUB)
        import memsearch
        col = memsearch._client().get_collection(memsearch.COLLECTION)
        col.delete(ids=[uid])
        return {'ok': True}
    except Exception as e:
        return {'error': str(e)[:120]}


def remember(content, type='fact', source='workbuddy', scope='shared', subject='user',
             confidence=0.8, tags='', status='active', mem0=False, valid_from=None):
    """写入/更新一条事实。若内容高度相似则更新，若冲突则 supersede。
    可选 mem0=True 时同步写入 Mem0 语义索引（自动提取+冲突消解）。

    valid_from: T 轴（有效时间）起点。默认 None → 取当前时刻，即假定"记录时即成立"。
      事后复盘类事实应**显式传入**：例如本库真实条目
      "通知问题定案(2026-09-11)" 是 09-13 才记录的，
      其 valid_from 应为 2026-09-11、recorded_at 为 09-13。
      ★这正是双时间轴存在的意义——让"事实何时成立"与"系统何时知道"分离，
        否则查"09-12 系统认为什么为真"会得出错误结论。
    """
    init_db()
    conn = get_conn()
    try:
        uid = _uid('fact', content + source)
        # 查重：相同 source + 高度相似内容
        existing = conn.execute(
            "SELECT uid FROM facts WHERE source=? AND status='active' AND content=?",
            (source, content)).fetchone()
        if existing:
            conn.execute("UPDATE facts SET updated_at=? WHERE uid=?", (now(), existing['uid']))
            audit(conn, 'remember_dup', existing['uid'], source, content[:80])
            conn.commit()
            return {'ok': True, 'uid': existing['uid'], 'op': 'noop_dup'}

        # 近义去重（2026-09-13）：向量相似度 + 文本相似度双确认，视为同一事实
        try:
            sys.path.insert(0, HUB)
            import memsearch
            import difflib as _dl
            col = memsearch._client().get_collection(memsearch.COLLECTION)
            qv = memsearch._embed([content])[0]
            rr = col.query(query_embeddings=[qv], n_results=3)
            for did, dist in zip(rr['ids'][0], rr['distances'][0]):
                sim = 1 - dist
                if sim < 0.92:
                    continue
                got = col.get(ids=[did])
                if not got['documents']:
                    continue
                doc = got['documents'][0]
                if _dl.SequenceMatcher(None, content, doc).ratio() >= 0.85:
                    conn.execute("UPDATE facts SET updated_at=? WHERE uid=?", (now(), did))
                    audit(conn, 'remember_near_dup', did, source, content[:60])
                    conn.commit()
                    return {'ok': True, 'uid': did, 'op': 'noop_near_dup',
                            'sim': round(sim, 3)}
        except Exception:
            pass

        ts = now()
        conn.execute(
            """INSERT INTO facts (uid,type,subject,content,status,source,scope,confidence,tags,
                                  created_at,updated_at,valid_from,recorded_at,temporal_source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, type, subject, content, status, source, scope, confidence, tags,
             ts, ts, valid_from or ts, ts, 'native'))
        audit(conn, 'remember', uid, source, content[:80])
        conn.commit()

        # 可选 Mem0 同步
        m0 = None
        if mem0:
            m0 = _mem0_add(content)

        # 自动同步向量索引（2026-09-13：消除"新记忆需手动 rebuild_index"缺口）
        vec = _vec_upsert(uid, content, type, source)

        return {'ok': True, 'uid': uid, 'op': 'insert', 'mem0': m0, 'vec': vec}
    finally:
        conn.close()


def correct(old_uid, new_content, reason, by_agent='workbuddy', valid_from=None):
    """用户纠正：旧事实 supersede，新事实 active。

    双时间轴处理：
      旧事实 valid_to = 新事实 valid_from（T 轴连续——旧事实止于新事实起，
        不留下"两边都不成立"的时间空档）
      旧事实 invalidated_at = 当前时刻（T' 轴——**我们此刻**才判定它失效，
        可能与 valid_to 不同；若纠正的是陈年旧事，两个值会明显分离）
    """
    init_db()
    conn = get_conn()
    try:
        old = conn.execute("SELECT * FROM facts WHERE uid=?", (old_uid,)).fetchone()
        if not old:
            return {'ok': False, 'error': 'old uid not found: %s' % old_uid}
        new_uid = _uid('fact', new_content + by_agent)
        ts = now()
        nvf = valid_from or ts          # 新事实的 T 轴起点
        # 旧事实标记 superseded
        conn.execute("UPDATE facts SET status='superseded', superseded_by=?, valid_to=?, "
                     "invalidated_at=?, updated_at=? WHERE uid=?",
                     (new_uid, nvf, ts, ts, old_uid))
        # 新事实写入
        conn.execute(
            """INSERT INTO facts (uid,type,subject,content,status,source,scope,confidence,
                                  created_at,updated_at,valid_from,recorded_at,temporal_source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_uid, old['type'], old['subject'], new_content, 'active',
             by_agent, old['scope'], 1.0, ts, ts, nvf, ts, 'native'))
        conn.execute("INSERT INTO supersessions (old_uid,new_uid,reason,by_agent,ts) VALUES (?,?,?,?,?)",
                     (old_uid, new_uid, reason, by_agent, now()))
        audit(conn, 'correct', old_uid, by_agent, '%s -> %s' % (old['content'][:40], new_content[:40]))
        conn.commit()
        # 向量索引同步：旧的下线、新的上线
        _vec_delete(old_uid)
        _vec_upsert(new_uid, new_content, old['type'], by_agent)
        return {'ok': True, 'old_uid': old_uid, 'new_uid': new_uid, 'op': 'supersede'}
    finally:
        conn.close()


def retire(uid, reason, by_agent='workbuddy'):
    """退役机制/工具。双时间轴：valid_to 与 invalidated_at 都记当前时刻
    （退役是"现在就判定不再有效"的动作，两轴在此重合；若某条事实是
     事后才补记退役，则应改用 governance.retire 并另行指定 valid_to）。"""
    init_db()
    conn = get_conn()
    try:
        ts = now()
        conn.execute("UPDATE facts SET status='retired', valid_to=?, invalidated_at=?, "
                     "updated_at=? WHERE uid=?",
                     (ts, ts, ts, uid))
        conn.execute("UPDATE tool_assets SET status='retired', updated_at=? WHERE uid=?",
                     (ts, uid))
        audit(conn, 'retire', uid, by_agent, reason)
        conn.commit()
        _vec_delete(uid)
        return {'ok': True, 'uid': uid, 'op': 'retired'}
    finally:
        conn.close()


def _norm_at(at):
    """把 as-of 时间点归一化为可比较的字符串。
    只给日期 → 视为当天末尾（"截至那天为止"的直觉语义）。"""
    at = (at or '').strip()
    if not at:
        return now()
    if len(at) == 10:
        return at + ' 23:59:59'
    if len(at) == 16:
        return at + ':59'
    return at


def as_of(at, kind='valid', ftype=None, subject=None, source=None, limit=200):
    """双时间轴 as-of 查询：回答"在某个时间点，什么是真的 / 系统知道什么"。

      kind='valid'  T 轴（有效时间）—— 现实世界在 at 时刻，哪些事实成立
                    条件 valid_from <= at AND (valid_to 为空 OR valid_to > at)
      kind='known'  T'轴（摄录时间）—— 系统在 at 时刻**认为**哪些事实成立
                    条件 recorded_at <= at AND (invalidated_at 为空 OR invalidated_at > at)

    ★两条轴的差异正是这套模型的价值。用本库真实事件说明：
      「DeepSeek 官方 API 已充值 10 元可用」现实里 2026-09-15 失效，
      但系统当晚 21:07 才发现并落库。
        问 2026-09-15 12:00 —
          kind='valid' → 已失效（现实如此）
          kind='known' → 仍认为"可用"（系统当时确实还不知道）
      只有同时具备两条轴，才能解释"当时为什么那样决策"，
      也才能避免用今天的信息去责备昨天的判断。
    """
    init_db()
    conn = get_conn()
    try:
        t = _norm_at(at)
        where, args = [], []
        if kind == 'valid':
            where.append("valid_from IS NOT NULL AND valid_from!='' AND valid_from<=?")
            args.append(t)
            where.append("(valid_to IS NULL OR valid_to='' OR valid_to>?)")
            args.append(t)
        else:                                   # known
            where.append("recorded_at IS NOT NULL AND recorded_at!='' AND recorded_at<=?")
            args.append(t)
            where.append("(invalidated_at IS NULL OR invalidated_at='' OR invalidated_at>?)")
            args.append(t)
        if ftype:
            where.append('type=?'); args.append(ftype)
        if subject:
            where.append('subject=?'); args.append(subject)
        if source:
            where.append('source=?'); args.append(source)
        sql = ("SELECT uid,type,subject,content,status,valid_from,valid_to,recorded_at,"
               "invalidated_at,temporal_source,superseded_by,source FROM facts WHERE "
               + ' AND '.join(where) + " ORDER BY COALESCE(valid_from,created_at) LIMIT ?")
        args.append(limit)
        rows = [dict(r) for r in conn.execute(sql, args)]
        # 附带"此刻仍为真"的条数，便于一眼看出历史与现状差多少
        try:
            live = conn.execute(
                "SELECT COUNT(*) FROM facts WHERE status='active'"
                + (" AND type=?" if ftype else '')
                + (" AND subject=?" if subject else '')
                + (" AND source=?" if source else ''),
                tuple(x for x in (ftype, subject, source) if x)).fetchone()[0]
        except Exception:
            live = None
        return {'at': t, 'kind': kind, 'count': len(rows),
                'active_now': live, 'rows': rows}
    finally:
        conn.close()


def timeline(uid, max_hops=20):
    """沿替代链还原一条事实的完整演化时间轴（forward: 它被谁取代）。"""
    init_db()
    conn = get_conn()
    try:
        chain, seen, cur = [], set(), uid
        while cur and cur not in seen and len(chain) < max_hops:
            seen.add(cur)
            r = conn.execute(
                "SELECT uid,status,content,valid_from,valid_to,recorded_at,invalidated_at,"
                "temporal_source,superseded_by FROM facts WHERE uid=?", (cur,)).fetchone()
            if not r:
                break
            chain.append(dict(r))
            cur = r['superseded_by']
        return chain
    finally:
        conn.close()


def record_tool(name, path=None, entrypoint=None, aliases='', type='local_tool',
                capabilities='', known_failures='[]', prerequisites='', source='workbuddy'):
    init_db()
    conn = get_conn()
    try:
        uid = _uid('tool', name)
        existing = conn.execute("SELECT uid FROM tool_assets WHERE name=?", (name,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE tool_assets SET path=?, entrypoint=?, aliases=?, capabilities=?,
                   known_failures=?, last_verified_at=?, updated_at=? WHERE uid=?""",
                (path, entrypoint, aliases, capabilities, known_failures, now(), now(), existing['uid']))
            audit(conn, 'record_tool_update', existing['uid'], source, name)
            conn.commit()
            return {'ok': True, 'uid': existing['uid'], 'op': 'update'}
        conn.execute(
            """INSERT INTO tool_assets (uid,name,aliases,type,status,path,entrypoint,capabilities,
               known_failures,prerequisites,last_verified_at,source,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, name, aliases, type, 'active', path, entrypoint, capabilities,
             known_failures, prerequisites, now(), source, now(), now()))
        audit(conn, 'record_tool', uid, source, name)
        conn.commit()
        return {'ok': True, 'uid': uid, 'op': 'insert'}
    finally:
        conn.close()


def search(query, limit=10, mem0=False):
    """混合检索（2026-09-13 升级版）：质量门禁 + 向量 + ASCII精确 + 融合排序。
    经 Astra 多轮协作确认；8 条回归查询 8/8 通过（旧 LIKE 版仅 4/8）。
    失败时自动退化到旧 LIKE 检索。"""
    try:
        sys.path.insert(0, HUB)
        import memsearch
        r = memsearch.search_hybrid(query, limit=limit)
        if r.get('results'):
            return {'ok': True, 'query': r['query'], 'results': r['results'],
                    'semantic': r['results'], 'engine': 'hybrid'}
        # 🔴 2026-09-15 修：向量库不可用（缺 chromadb）时 hybrid 会**成功返回空列表**
        #    ——不是抛异常。旧实现把空结果直接当结果返回，调用方会误判「中枢里没有这条记忆」。
        #    只要 hybrid 没给出结果，一律退化到 LIKE 兜底。
        _why = 'hybrid 返回空（向量库不可用？）'
    except Exception as e:
        _why = '%s: %s' % (type(e).__name__, str(e)[:60])
    r = _search_like_legacy(query, limit, mem0)
    # 🔴 兜底再兜底：LIKE 是**整串子串**匹配，「记忆中枢 投影 容量」这种多词查询永远命中不了。
    #    缺向量库时这就是唯一检索路径，故按空格/顿号拆词逐词再查，合并去重。
    if not r.get('results'):
        import re as _re2
        _toks = [t for t in _re2.split(r'[\s,，、;；]+', query.strip()) if len(t) >= 2]
        if len(_toks) > 1:
            _merged = {}
            for _t in _toks:
                for _it in (_search_like_legacy(_t, limit, mem0).get('results') or []):
                    _k = _it.get('uid') if isinstance(_it, dict) else str(_it)[:64]
                    _merged.setdefault(_k, _it)
            if _merged:
                r['results'] = list(_merged.values())[:limit]
                _why += ' + 拆词合并'
    r['engine'] = 'like_fallback (%s)' % _why
    return r


def _search_like_legacy(query, limit=10, mem0=False):
    """[旧版，保留作兜底] 关键词 LIKE 精确子串匹配（中文失效，仅 ASCII 有效）。"""
    init_db()
    conn = get_conn()
    try:
        q = query.strip()
        results = []
        # 1) 事实：关键词 LIKE（active 优先，superseded 惩罚）
        for row in conn.execute(
            "SELECT * FROM facts WHERE status='active' AND (content LIKE ? OR subject LIKE ? OR tags LIKE ?) ORDER BY updated_at DESC LIMIT ?",
            ('%' + q + '%', '%' + q + '%', '%' + q + '%', limit)).fetchall():
            results.append({'kind': 'fact', 'uid': row['uid'], 'type': row['type'],
                            'content': row['content'], 'status': row['status'],
                            'source': row['source'], 'score': 1.0})
        # 2) 工具：名字/别名/路径/能力匹配
        for row in conn.execute(
            "SELECT * FROM tool_assets WHERE status='active' AND (name LIKE ? OR aliases LIKE ? OR path LIKE ? OR capabilities LIKE ?) LIMIT ?",
            ('%' + q + '%', '%' + q + '%', '%' + q + '%', '%' + q + '%', limit)).fetchall():
            results.append({'kind': 'tool', 'uid': row['uid'], 'name': row['name'],
                            'path': row['path'], 'entrypoint': row['entrypoint'],
                            'known_failures': row['known_failures'], 'score': 1.0})
        # 3) 配方：任务触发词匹配
        for row in conn.execute(
            "SELECT * FROM recipes WHERE task_patterns LIKE ? LIMIT ?",
            ('%' + q + '%', limit)).fetchall():
            results.append({'kind': 'recipe', 'uid': row['uid'],
                            'task_patterns': row['task_patterns'],
                            'preferred_tool': row['preferred_tool'],
                            'steps': row['steps'], 'known_pitfalls': row['known_pitfalls'],
                            'score': 1.0})
        # 4) Mem0 语义检索（可选）
        mem0_hits = []
        if mem0:
            try:
                sys.path.insert(0, HUB)
                from mem0 import Memory
                import mem0_config
                m = Memory.from_config(mem0_config.MEM0_CONFIG)
                s = m.search(q, filters={'user_id': 'wzj'}, limit=limit)
                for r in s.get('results', []):
                    mem0_hits.append({'kind': 'semantic', 'content': r.get('memory'),
                                      'score': round(r.get('score', 0), 3)})
            except Exception:
                pass
        return {'ok': True, 'query': q, 'results': results, 'semantic': mem0_hits}
    finally:
        conn.close()


def resolve_task(task):
    """按任务名返回完整执行配方。"""
    init_db()
    conn = get_conn()
    try:
        out = {'task': task, 'recipe': None, 'preferred_tools': [], 'paths': [],
               'endpoints': [], 'known_pitfalls': [], 'fallbacks': [], 'active_facts': []}
        # 找配方
        rec = conn.execute("SELECT * FROM recipes").fetchall()
        for r in rec:
            if task in (r['task_patterns'] or ''):
                out['recipe'] = dict(r)
                out['known_pitfalls'] = json.loads(r['known_pitfalls'] or '[]')
                out['fallbacks'] = json.loads(r['fallback_tools'] or '[]')
                break
        # 相关事实
        for row in conn.execute(
            "SELECT * FROM facts WHERE status='active' AND content LIKE ? LIMIT 5",
            ('%' + task + '%',)).fetchall():
            out['active_facts'].append({'content': row['content'], 'source': row['source']})
        # 相关工具
        for row in conn.execute(
            "SELECT * FROM tool_assets WHERE status='active' AND (name LIKE ? OR aliases LIKE ? OR capabilities LIKE ?) LIMIT 5",
            ('%' + task + '%', '%' + task + '%', '%' + task + '%')).fetchall():
            out['preferred_tools'].append({'name': row['name'], 'path': row['path'],
                                           'entrypoint': row['entrypoint'],
                                           'known_failures': row['known_failures']})
        return out
    finally:
        conn.close()


# =====================================================================
# 记忆自动闭环（astra 裁决 2026-09-13）
# 链路：agent → record_event(写队列) → process_events(异步) →
#       auto_reflect(deepseek提炼) → commit_memory_candidate(过滤/去重) → remember
# =====================================================================

def _llm_json(prompt, system='', model='deepseek', temperature=0.1, max_tokens=2000):
    """用指定通道做一次 JSON 结构化提炼。返回 dict 或 None。"""
    import urllib.request
    try:
        sys.path.insert(0, r'<AUDIT>')
        import cred_env
        cred_env.env()
        if model == 'deepseek':
            key = os.environ.get('DEEPSEEK_OFFICIAL_KEY', '')
            base = 'https://api.deepseek.com/v1/chat/completions'
            mname = 'deepseek-chat'
        else:  # astra
            key = os.environ.get('GPTX_ASTRA_KEY', '')
            base = 'https://api.gptx.cc/v1/chat/completions'
            mname = 'gpt-6-astra'
        if not key:
            return None
        msgs = []
        if system:
            msgs.append({'role': 'system', 'content': system})
        msgs.append({'role': 'user', 'content': prompt})
        body = {'model': mname, 'messages': msgs, 'temperature': temperature, 'max_tokens': max_tokens}
        req = urllib.request.Request(base, data=json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        r = urllib.request.urlopen(req, timeout=120)
        d = json.loads(r.read().decode())
        content = d['choices'][0]['message']['content']
        # 剥离可能包裹的 ```json ... ``` 代码块
        content = content.strip()
        if content.startswith('```'):
            content = content.split('\n', 1)[-1]
            if content.endswith('```'):
                content = content[:-3]
        return json.loads(content)
    except Exception as e:
        return {'_error': str(e)[:200]}


_REFLECT_SYSTEM = (
    '你是长期记忆候选提炼器。根据任务摘要，只输出 JSON，不要输出任何其他文字。'
    '格式：{"memories":[{"text":"...","type":"technical_pitfall|verified_solution|tool_path|decision|preference","'
    'scope":"...","confidence":0.0,"reuse_score":0.0,"impact_score":0.0,"evidence":"..."}]}。'
    '只保留未来可能复用、且有执行证据或明确用户确认的结论。'
    '不要记录普通步骤、临时状态、猜测、一次性文件名、进程是否运行。'
    '如果没有值得记录的内容，返回 {"memories":[]}。'
)


def record_event(event_type, run_id, payload, agent=None):
    """只写 SQLite 事件队列，不直接写长期记忆。幂等：同 run_id+event_type 只记一次。"""
    init_db()
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM run_events WHERE run_id=? AND event_type=?",
            (run_id, event_type)).fetchone()
        if existing:
            return {'ok': True, 'event_id': existing['id'], 'op': 'noop_dup'}
        cur = conn.execute(
            "INSERT INTO run_events (run_id,event_type,agent,payload_json,status,created_at) VALUES (?,?,?,?,?,?)",
            (run_id, event_type, agent, json.dumps(payload, ensure_ascii=False), 'pending', now()))
        audit(conn, 'record_event', str(cur.lastrowid), agent or '', '%s:%s' % (event_type, run_id))
        conn.commit()
        return {'ok': True, 'event_id': cur.lastrowid, 'op': 'insert'}
    finally:
        conn.close()


def _value_score(m):
    """astra 公式：0.4*reuse + 0.3*impact + 0.2*evidence + 0.1*stability。"""
    reuse = float(m.get('reuse_score', 0.5))
    impact = float(m.get('impact_score', 0.5))
    ev = 0.9 if m.get('evidence') else 0.3  # 有证据权重高
    stability = float(m.get('stability', 0.7))
    return 0.4 * reuse + 0.3 * impact + 0.2 * ev + 0.1 * stability


def commit_memory_candidate(candidate, source_agent='reflector'):
    """校验/价值判断/去重后写入。返回写入结果。"""
    text = (candidate.get('text') or '').strip()
    if not text:
        return {'ok': False, 'reason': 'empty_text'}
    conf = float(candidate.get('confidence', 0.7))
    vs = _value_score(candidate)
    # 阈值：value>=0.75 且 conf>=0.8 才自动写；否则入 candidates 待审
    if vs < 0.75 or conf < 0.8:
        init_db(); conn = get_conn()
        try:
            conn.execute("INSERT INTO candidates (content,type,reason,source,ts) VALUES (?,?,?,?,?)",
                         (text, candidate.get('type', 'fact'),
                          'value=%.2f conf=%.2f 低于阈值' % (vs, conf), source_agent, now()))
            conn.commit()
            return {'ok': True, 'op': 'candidate_queued', 'value_score': round(vs, 2)}
        finally:
            conn.close()
    # 去重：精确内容已存在则跳过
    init_db(); conn = get_conn()
    try:
        dup = conn.execute("SELECT uid FROM facts WHERE content=? AND status='active'", (text,)).fetchone()
        if dup:
            conn.execute("UPDATE facts SET updated_at=? WHERE uid=?", (now(), dup['uid']))
            conn.commit()
            return {'ok': True, 'op': 'noop_dup', 'uid': dup['uid']}
    finally:
        conn.close()
    # 写入（同步 Mem0 语义索引，让语义检索也能召回）
    typ = candidate.get('type', 'fact')
    if typ not in ('fact', 'decision', 'incident', 'preference', 'environment', 'experience'):
        typ = 'fact'
    return remember(text, type=typ, source=source_agent, confidence=conf,
                    tags=(candidate.get('scope') or ''), mem0=True)


def auto_reflect(run_id, payload, model='deepseek'):
    """读运行摘要，deepseek 提炼候选记忆并提交。返回提炼与写入结果。"""
    prompt = ('任务摘要如下，请提炼可复用的长期记忆候选（严格按 JSON 格式）：\n'
              + json.dumps(payload, ensure_ascii=False)[:8000])
    res = _llm_json(prompt, system=_REFLECT_SYSTEM, model=model)
    if res is None or '_error' in res:
        return {'ok': False, 'error': (res or {}).get('_error', 'llm_failed')}
    memories = res.get('memories', [])
    written = []
    for m in memories:
        r = commit_memory_candidate(m, source_agent='reflector')
        written.append({'text': m.get('text', '')[:80], 'result': r})
    return {'ok': True, 'run_id': run_id, 'candidates': len(memories), 'written': written}


def process_events(limit=10):
    """读 pending 事件，逐个 auto_reflect，更新状态。返回处理摘要。"""
    init_db()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE status='pending' ORDER BY created_at ASC LIMIT ?",
            (limit,)).fetchall()
        out = {'processed': 0, 'skipped': 0, 'failed': 0, 'details': []}
        for row in rows:
            try:
                payload = json.loads(row['payload_json'])
                r = auto_reflect(row['run_id'], payload, model='deepseek')
                if r.get('ok'):
                    conn.execute("UPDATE run_events SET status='processed', processed_at=? WHERE id=?",
                                 (now(), row['id']))
                    out['processed'] += 1
                else:
                    conn.execute("UPDATE run_events SET status='failed', error=?, retry_count=retry_count+1 WHERE id=?",
                                 ((r.get('error') or '')[:200], row['id']))
                    out['failed'] += 1
                out['details'].append({'run_id': row['run_id'], 'ok': r.get('ok'),
                                       'candidates': r.get('candidates', 0)})
            except Exception as e:
                conn.execute("UPDATE run_events SET status='failed', error=?, retry_count=retry_count+1 WHERE id=?",
                             (str(e)[:200], row['id']))
                out['failed'] += 1
        conn.commit()
        return out
    finally:
        conn.close()


def incident(step, error, workaround='', result='', run_id=None, agent='workbuddy'):
    """出错事件即时记录（astra 方案占 30%）。写入 run_events，由反射器提炼。
    触发场景：命令非零退出、同一步骤重试≥2次、用户说"还是不行"、工具被策略阻断、改用替代方案。"""
    payload = {'kind': 'incident', 'step': step, 'error': error,
               'workaround': workaround, 'result': result}
    rid = run_id or ('incident-%s' % time.strftime('%Y%m%d%H%M%S'))
    return record_event('incident', rid, payload, agent=agent)


def on_miss(query, task_type='', answer_source='agent', force=False):
    """检索 miss 记录（astra 方案占 10%）。当 recall 结果为空或分数过低时调用，进待补录队列。
    默认会先自查：若检索其实有结果则跳过（避免误报）。"""
    top = 0.0
    if not force:
        try:
            r = search(query, limit=3)
            top = r['results'][0]['score'] if r.get('results') else 0.0
            if top >= 0.55:
                return {'ok': True, 'op': 'skip', 'reason': '检索有结果(score=%.3f)' % top,
                        'top_score': top}
        except Exception:
            pass
    payload = {'kind': 'retrieval_miss', 'query': query, 'task_type': task_type,
               'top_score': top, 'answer_source': answer_source}
    rid = 'miss-%s' % hashlib.md5(query.encode('utf-8')).hexdigest()[:10]
    res = record_event('retrieval_miss', rid, payload, agent=answer_source)
    res['top_score'] = top
    return res


def stats():
    init_db()
    conn = get_conn()
    try:
        out = {}
        for tbl in ('facts', 'tool_assets', 'recipes', 'supersessions', 'audit_log', 'candidates'):
            out[tbl] = conn.execute('SELECT COUNT(*) c FROM %s' % tbl).fetchone()['c']
        out['facts_by_status'] = {r['status']: r['c'] for r in
                                  conn.execute('SELECT status, COUNT(*) c FROM facts GROUP BY status').fetchall()}
        return out
    finally:
        conn.close()


def rebuild():
    """从 SQLite 重建所有投影：sink.json + 各 agent 投影 + ~/.workbuddy/MEMORY.md。"""
    init_db()
    conn = get_conn()
    try:
        # 1) 生成 sink.json（兼容导出）
        sink = {'fact': [], 'decision': [], 'incident': [], 'experience': [], 'todo': []}
        for row in conn.execute("SELECT * FROM facts WHERE status='active' ORDER BY updated_at DESC").fetchall():
            typ = row['type'] if row['type'] in sink else 'fact'
            sink[typ].append({
                'text': row['content'], 'source': row['source'], 'tag': row['tags'] or '',
                'ts': row['updated_at'] or row['created_at'], 'status': row['status'],
                'uid': row['uid'], 'confidence': row['confidence'],
            })
        with open(SINK, 'w', encoding='utf-8') as f:
            json.dump(sink, f, ensure_ascii=False, indent=2)

        # 2) 生成 WorkBuddy MEMORY.md
        mem_lines = [
            '# MEMORY.md — 记忆中枢投影（导航版）',
            '<!-- 真源：%s；由 gateway.py rebuild 生成，勿手改 -->' % os.path.join(HUB, 'memory.db'),
            '',
            '## 怎么用',
            '- 取全文：cd %s && python mem.py search "<关键词>"' % HUB.replace('\\', '/'),
            '- 写记忆：python gateway.py remember "<内容>" --type fact|decision|incident|experience',
            '- 禁止直接改本文件与 sink.json，一律走 gateway.py。',
            '- 下全称否定结论前先全盘搜索；动手前可用 resolve_task 取工具配方。',
            '',
            '## 关键事实（active，新→旧，每条仅首句结论）',
        ]
        # 关键事实：**按类型配额**选，再按时间倒序合并。
        # 🔴 2026-09-14 修：旧实现是 `type IN ('fact','decision') LIMIT 80`
        #    → incident / experience **被整体丢弃**（实测 216 条 active 里 70 条永远进不了投影，
        #      其中包括 ACL 拒写的坑、判驱动/判病毒的方法论、会话卡顿真因 —— 恰恰是最该被记住的）。
        #    另外注入侧会按体积截断，所以顺序必须"新→旧"，这样截断只丢最老的。
        _QUOTA = (('decision', 40), ('incident', 20), ('fact', 45), ('experience', 25))
        _picked = []
        for _typ, _q in _QUOTA:
            # 🔴 2026-09-15 四修：remember() 写入时不填 updated_at（只有 supersede 才更新它），
            #    而 SQLite 里 NULL 最小、DESC 时排**最末** → 新记忆永远排在队尾，
            #    预算一满就被整体砍掉。实测 16:05 写入的条目 rebuild 后不在投影里（最后一条是 09-14 的）。
            #    故统一按 COALESCE(updated_at, created_at) 排序，保证「新写的先进投影」。
            for _r in conn.execute(
                    "SELECT * FROM facts WHERE status='active' AND type=? "
                    "ORDER BY COALESCE(NULLIF(updated_at,''), created_at) DESC LIMIT ?",
                    (_typ, _q)).fetchall():
                _picked.append((_r['updated_at'] or _r['created_at'] or '', _typ, _r))
        _picked.sort(key=lambda p: p[0], reverse=True)
        #    🔴 2026-09-14 再修：注入侧按体积截断，一条动辄 1500 字的"巨型事实"会把预算吃光
        #       （实测前 8 条就把额度用完，其余全被砍）。故对单条做软截断到 700 字，
        #       让同样预算能覆盖 3~4 倍的**不同**记忆。全文仍在 memory.db，用 mem.py search 可取回。
        #    🔴 2026-09-15 三修：官方 MEMORY.md 槽位是**会话级注入口**，设计容量约 4000 字符。
        #       旧版把 73KB 投影塞进去 → 实测注入时被整体砍到只剩 4 条，且每条都是 700 字残片。
        #       **记得越多反而注入越少**。故改为「导航版」：每条只留首句结论（自含），
        #       总预算硬守官方限额；全文一律走 mem.py search 按需取回。
        import re as _re
        _BUDGET = int(os.environ.get('MEM_PROJ_BUDGET', '3980'))
        #    🔴 2026-09-15 四修：CAP=60 实测 43/43 全部「不以句号结尾」——首句普遍超过 60 字，
        #       于是每条都被硬砍成半句话，连「首句要写自含结论」这条约定自己都被腰斩。
        #       模拟对比（active 全量）：CAP60→50条/12条硬砍，CAP100→42/5，CAP130→41/2，
        #       **CAP160→39条/0条硬砍（拐点）**，CAP200→38/0（收益递减）。
        #       取 160：少装 11 条换「条条完整」，因为半句话无法据以判断是否要取回全文。
        #       另：截断位置改为**回退到最近的句读**，避免出现「…决定中枢上限的四个硬约」这种断头。
        _LINE_CAP = 160

        def _lead(txt):
            t = _re.sub(r'^【[^】]*】', '', (txt or '').strip()).strip()
            m = _re.search(r'[。；;！!？?\n]', t)
            if m and 0 < m.start() < _LINE_CAP:
                t = t[:m.start()].strip()
            elif len(t) > _LINE_CAP:
                seg = t[:_LINE_CAP]
                best = -1
                for _p in '。；，、：;:！？':
                    _i = seg.rfind(_p)
                    if _i > best:
                        best = _i
                if best > _LINE_CAP * 0.5:
                    t = seg[:best + 1] + '…'
                else:
                    t = seg.rstrip() + '…'
            return t or '(空)'

        _tail = ['', '## 工具资产（active）']
        # ★2026-09-15：资产增到 43 条，全量投影会挤爆 4000 槽位。
        # 顺序：先保事实（上方 mem_lines），资产按剩余预算填；
        # 带 ★ 的易错资产优先，其余截断并给检索提示。
        _prio, _rest = [], []
        for _row in conn.execute(
                "SELECT * FROM tool_assets WHERE status='active' ORDER BY name").fetchall():
            _line = '- %s → %s' % (_row['name'], (_row['path'] or _row['entrypoint'] or '')[:58])
            (_prio if '★' in (_row['prerequisites'] or '') else _rest).append(_line)

        _tail.extend(_prio)
        _room = _BUDGET - sum(len(x) + 1 for x in mem_lines) - sum(len(x) + 1 for x in _tail) - 90

        # ★2026-09-15 五修：实测投影 4020 字符 / 预算 4000 → **超 20 字符**。
        #   根因：页脚那行（"导航版：已列 N 条…"）是在**预算检查之后**才 append 的，
        #   它没有被任何 _room 约束覆盖；同时 _room 可能被前两项算成负数。
        #   而超预算的后果很严重：官方注入时**整体截断**，不是截掉末尾。
        #   修法：① 页脚预留固定额度；② 所有 _room 取值加 max(0, ...)；
        #        ③ 收尾做一次**硬裁**，确保 len(mem_text) <= _BUDGET。
        _FOOTER_RESERVE = 110
        _room = max(0, _room - _FOOTER_RESERVE)

        _kept = 0
        for _ts, _typ, _r in _picked:
            _d = (_r['updated_at'] or _r['created_at'] or '')[:10] or '????-??-??'
            _item = '- [%s|%s] %s' % (_d, _typ, _lead(_r['content']))
            if len(_item) + 1 > _room:
                break
            mem_lines.append(_item)
            _room -= len(_item) + 1
            _kept += 1

        # 事实填完后，把剩余预算给非优先资产
        _extra = []
        for _line in _rest:
            if len(_line) + 1 > _room:
                _extra.append('- …另 %d 条资产见网关库（mem.py search 或 tool_audit.py audit）'
                              % (len(_rest) - len(_extra)))
                break
            _extra.append(_line)
            _room -= len(_line) + 1
        _tail.extend(_extra)

        mem_lines.extend(_tail)
        mem_lines.append('')
        mem_lines.append('> 导航版：已列 %d 条 / active 共 %d 条（受官方 4000 字符槽位硬限，'
                        '写多会被整体截断）；其余全文用 mem.py search 取。'
                        % (_kept, sum(len(v) for v in sink.values())))
        mem_text = '\n'.join(mem_lines)

        # ★硬裁保险：无论上游怎么算，最终产物绝不超过 _BUDGET。
        #   超了就从**事实区**末尾往回删整行（保留页脚，因为它告知读者"还有更多"）。
        if len(mem_text) > _BUDGET:
            _foot = mem_lines[-2:]
            _body = mem_lines[:-2]
            while _body and len('\n'.join(_body + _foot)) > _BUDGET:
                _body.pop()
            mem_text = '\n'.join(_body + _foot)
            if len(mem_text) > _BUDGET:      # 极端情况：连页脚都放不下，砍到纯粹硬上限
                mem_text = mem_text[:_BUDGET]

        wb = os.path.expanduser(r'~\.workbuddy\MEMORY.md')
        os.makedirs(os.path.dirname(wb), exist_ok=True)
        with open(wb, 'w', encoding='utf-8') as f:
            f.write(mem_text)

        conn.commit()
        return {'ok': True, 'sink': SINK, 'mem': wb, 'facts': len(sink['fact']),
                'tools': len(conn.execute("SELECT id FROM tool_assets WHERE status='active'").fetchall())}
    finally:
        conn.close()


def _env_guard():
    """★2026-09-15：解释器守卫（与 mem.py 同一目的）。

    检索依赖 chromadb/numpy，只在 .venv-memory 里。用默认 python 跑会**静默降级**
    成纯关键词检索——66 条资产查不到的坑就是这么来的。
    """
    try:
        miss = []
        for m in ('chromadb', 'numpy'):
            try:
                __import__(m)
            except Exception:
                miss.append(m)
        if miss:
            sys.stderr.write(
                '\n[!] 当前解释器缺 %s，检索将降级为纯关键词（资产类查询大概率查不到）。\n'
                '    正确: <HUB>\\.venv-memory\\Scripts\\python.exe\n\n'
                % ', '.join(miss))
    except Exception:
        pass


def main():
    _env_guard()
    ap = argparse.ArgumentParser(description='Memory Gateway — 三 agent 唯一记忆入口')
    sub = ap.add_subparsers(dest='cmd')

    r = sub.add_parser('remember'); r.add_argument('content'); r.add_argument('--type', default='fact')
    r.add_argument('--source', default='workbuddy'); r.add_argument('--scope', default='shared')
    r.add_argument('--subject', default='user'); r.add_argument('--tags', default='')

    s = sub.add_parser('search'); s.add_argument('query'); s.add_argument('--limit', type=int, default=10)
    s.add_argument('--mem0', action='store_true')

    g = sub.add_parser('get'); g.add_argument('uid')

    c = sub.add_parser('correct'); c.add_argument('old_uid'); c.add_argument('new_content'); c.add_argument('--reason', default='')

    rt = sub.add_parser('retire'); rt.add_argument('uid'); rt.add_argument('--reason', default='')

    t = sub.add_parser('record_tool'); t.add_argument('name'); t.add_argument('--path'); t.add_argument('--entrypoint')
    t.add_argument('--aliases', default=''); t.add_argument('--type', default='local_tool')
    t.add_argument('--capabilities', default=''); t.add_argument('--known_failures', default='[]')

    rk = sub.add_parser('resolve_task'); rk.add_argument('task')

    # 记忆自动闭环
    ev = sub.add_parser('event'); ev.add_argument('--type', required=True); ev.add_argument('--run-id', required=True)
    ev.add_argument('--agent', default=None); ev.add_argument('--payload-file', required=True)

    ar = sub.add_parser('auto_reflect'); ar.add_argument('--run-id'); ar.add_argument('--payload-file')

    pe = sub.add_parser('process_events'); pe.add_argument('--limit', type=int, default=10)

    ic = sub.add_parser('incident')
    ic.add_argument('--step', required=True); ic.add_argument('--error', required=True)
    ic.add_argument('--workaround', default=''); ic.add_argument('--result', default='')
    ic.add_argument('--run-id', default=None); ic.add_argument('--agent', default='workbuddy')

    om = sub.add_parser('on_miss'); om.add_argument('query')
    om.add_argument('--task-type', default=''); om.add_argument('--answer-source', default='agent')
    om.add_argument('--force', action='store_true')

    sub.add_parser('rebuild_index')

    sub.add_parser('stats')
    sub.add_parser('init')
    sub.add_parser('rebuild')

    # 资产审计与评测（见 tool_audit.py / asset_bench.py）
    sub.add_parser('assetaudit')
    sub.add_parser('assetseed')
    sub.add_parser('assetverify')
    sub.add_parser('assetbench')

    # ★治理层（见 governance.py）：冲突检测 / 时间衰减 / 退役
    sub.add_parser('govern')                       # 治理层体检（只读）
    gc = sub.add_parser('conflicts')               # 候选冲突（启发式，供人工复核）
    gc.add_argument('--days', type=int, default=30)
    sub.add_parser('conflicts_exact')              # ★精确冲突（显式状态断言，可放心自动化）
    sc = sub.add_parser('selfcheck'); sc.add_argument('--days', type=int, default=30)
    sub.add_parser('stale')                        # 过期候选（只读）

    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        return

    if a.cmd == 'init':
        init_db(); print(json.dumps({'ok': True, 'db': DB}, ensure_ascii=False))
    elif a.cmd == 'remember':
        print(json.dumps(remember(a.content, a.type, a.source, a.scope, a.subject, tags=a.tags), ensure_ascii=False))
    elif a.cmd == 'search':
        print(json.dumps(search(a.query, a.limit, mem0=a.mem0), ensure_ascii=False))
    elif a.cmd == 'get':
        init_db(); conn = get_conn()
        r = conn.execute('SELECT * FROM facts WHERE uid=?', (a.uid,)).fetchone()
        print(json.dumps(dict(r) if r else {'error': 'not found'}, ensure_ascii=False))
        conn.close()
    elif a.cmd == 'correct':
        print(json.dumps(correct(a.old_uid, a.new_content, a.reason), ensure_ascii=False))
    elif a.cmd == 'retire':
        print(json.dumps(retire(a.uid, a.reason), ensure_ascii=False))
    elif a.cmd == 'record_tool':
        print(json.dumps(record_tool(a.name, a.path, a.entrypoint, a.aliases, a.type,
                                     a.capabilities, a.known_failures, source='workbuddy'), ensure_ascii=False))
    elif a.cmd == 'resolve_task':
        print(json.dumps(resolve_task(a.task), ensure_ascii=False))
    elif a.cmd == 'event':
        with open(a.payload_file, encoding='utf-8') as f:
            payload = json.load(f)
        print(json.dumps(record_event(a.type, a.run_id, payload, agent=a.agent), ensure_ascii=False))
    elif a.cmd == 'auto_reflect':
        with open(a.payload_file, encoding='utf-8') as f:
            payload = json.load(f)
        print(json.dumps(auto_reflect(a.run_id, payload), ensure_ascii=False))
    elif a.cmd == 'process_events':
        print(json.dumps(process_events(a.limit), ensure_ascii=False))
    elif a.cmd == 'incident':
        print(json.dumps(incident(a.step, a.error, a.workaround, a.result, a.run_id, a.agent),
                         ensure_ascii=False))
    elif a.cmd == 'on_miss':
        print(json.dumps(on_miss(a.query, a.task_type, a.answer_source, a.force), ensure_ascii=False))
    elif a.cmd == 'rebuild_index':
        sys.path.insert(0, HUB)
        import memsearch
        print(json.dumps(memsearch.rebuild_vector_index(), ensure_ascii=False))
    elif a.cmd == 'stats':
        print(json.dumps(stats(), ensure_ascii=False))
    elif a.cmd == 'rebuild':
        print(json.dumps(rebuild(), ensure_ascii=False))
    elif a.cmd == 'assetaudit':
        import tool_audit
        tool_audit.audit()
    elif a.cmd == 'assetseed':
        import tool_audit
        tool_audit.seed()
    elif a.cmd == 'assetverify':
        import tool_audit
        tool_audit.verify()
    elif a.cmd == 'assetbench':
        import asset_bench
        import asset_bench_holdout
        print("\n【主集】")
        asset_bench.run(verbose=False)
        print("\n【留出集】")
        asset_bench_holdout.run(verbose=False)
    elif a.cmd == 'govern':
        import governance
        governance.audit()
    elif a.cmd == 'conflicts':
        import governance
        r = governance.find_conflicts(days_window=a.days)
        print(json.dumps({'ok': True, 'count': len(r), 'candidates': [
            {'a': x['a']['uid'], 'b': x['b']['uid'], 'shared': x['shared'],
             'a_text': x['a']['text'][:120], 'b_text': x['b']['text'][:120]}
            for x in r[:20]]}, ensure_ascii=False, indent=2))
    elif a.cmd == 'conflicts_exact':
        import governance
        r = governance.detect_explicit_conflicts()
        print(json.dumps({'ok': True, 'count': len(r), 'conflicts': [
            {'entity': x['entity'], 'newer': x['newer'],
             'suggest_retire': x['suggest_retire'], 'keep': x['keep'],
             'pos_text': x['pos']['sent'][:150], 'pos_ts': x['pos']['ts'],
             'neg_text': x['neg']['sent'][:150], 'neg_ts': x['neg']['ts']}
            for x in r]}, ensure_ascii=False, indent=2))
    elif a.cmd == 'selfcheck':
        import governance
        r = governance.find_self_contradictions()
        print(json.dumps({'ok': True, 'count': len(r), 'items': [
            {'uid': x['uid'], 'entity': x.get('entity'), 'shared': x['shared'],
             'pos': x['pos']['text'][:120], 'neg': x['neg']['text'][:120]}
            for x in r[:20]]}, ensure_ascii=False, indent=2))
    elif a.cmd == 'stale':
        import governance
        r = governance.find_stale(days=30)
        print(json.dumps({'ok': True, 'count': len(r), 'items': r[:20]},
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
