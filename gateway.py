# -*- coding: utf-8 -*-
"""
gateway.py — Memory Gateway（唯一记忆入口）

定位：多个异构 AI 客户端（各厂商、各版本、各账号）统一读写记忆的唯一入口。
分层：
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
  pin          投影钉住：把"必须一直在"的定义类结论排除在时间竞争之外
  qvalue       ★升级 1：回写/查看记忆的「被采纳价值分」（Q-Value）
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
import re
import argparse
from pathlib import Path

# ---------------------------------------------------------------------------
# hubguard —— 并发治理（**可选依赖**）
#   解决问题①（两个客户端并发写 = 后写覆盖前写，无检测无拒绝无通知）
#   解决问题②（投影逐条不保留 source → 看投影分不清谁写的）
#   解决问题③（工作区 memory/ 与另一实例同一物理文件 → 整体重写互相覆盖）
# ★为什么做成可选：hubguard.py 是新增文件。若有人只拷走 gateway.py，
#   不能让 import 失败把整个网关拖死 —— 缺失时全部降级为**原有行为**并置 HG=None。
# ★为什么用"包装"而不是改函数体：本机存在两个不同版本的 gateway.py
#   （memory_hub 在制品 72550 B / 本仓发布版），逐行打补丁必然对不齐；
#   包装器只依赖"函数名存在"，对两版都适用（见 hubguard.install_guards 的 docstring）。
try:
    import hubguard as HG
except Exception:                       # pragma: no cover - 无 hubguard 时照旧跑
    HG = None

# ---------------------------------------------------------------------------
# 默认来源标识（source）
#   中性值 `local` —— 与本机部署无关，陌生人装完即用，不会把记忆归到一个
#   他不认识的名字下。单机/自建环境可用环境变量覆盖，例如：
#       export MEM_DEFAULT_SOURCE=workbuddy
#   ★这是默认值，不是限制：每条记忆都仍应显式传 --source（多 agent 归属的前提）。
# ---------------------------------------------------------------------------
DEFAULT_SOURCE = os.environ.get('MEM_DEFAULT_SOURCE', 'local')
_anchor_warned = False  # a75-r3: auto-anchor failure warned-once flag
_schema_fp_warned = False  # 2026-11-07: schema-drift warned-once flag


def _hg_fact_line(date10, typ, source, lead):
    """投影事实行的唯一出口。有 hubguard 时带 `类型·来源` 标记。"""
    if HG is not None:
        return HG.format_fact_line(date10, typ, source, lead)
    return '- [%s|%s] %s' % (date10, typ, lead)


def _proj_targets():
    """投影目标列表。

    ★为什么不用 `HG.proj_paths() if HG else [硬编码]`：那样一旦 hubguard 缺失，
      MEM_PROJ_PATH 隔离**同时失效** —— 想模拟"旧版生成器"就必然写线上投影。
      2026-09-17 实测踩到：演练里把 HG 置 None，结果直接覆盖了线上 MEMORY.md。
      故这里自己实现同一契约：不设 MEM_PROJ_PATH 时与原来那一个硬编码路径**完全相同**。
    """
    if HG is not None:
        return HG.proj_paths()
    v = os.environ.get('MEM_PROJ_PATH')
    if v:
        return [x for x in v.split(os.pathsep) if x]
    return [os.path.expanduser(r'~\.workbuddy\MEMORY.md')]


_TAG_RE = None


def _count_tagged(text):
    """数投影里带「来源标记」的事实行（形如 `- [2026-09-17|exp·a] …`）。

    ★刻意**不依赖 hubguard**：最需要被抓住的场景恰恰是"hubguard.py 没装"，
      若这里调 HG.parse_fact_line，那个场景反而检查不了（自证盲区）。
    """
    global _TAG_RE
    if _TAG_RE is None:
        import re as _re2
        _TAG_RE = _re2.compile(r'^- \[\d{4}-\d{2}-\d{2}\|[^\]·]+·[^\]·]\]')
    return sum(1 for _l in (text or '').split('\n') if _TAG_RE.match(_l))


def _hg_write(path, text, before=None, on_conflict='abort', tag=''):
    """投影落盘。有 hubguard 走原子写（★跟随目标现有换行风格，见下）。

    ★2026-09-17（问题③收口）：带 `before` 时改走 `commit_guarded` —— 先证明
      "我读到的还是现在这个"，被别人改过就**拒写**（抛 ConcurrentModification）。
      只做 `atomic_write` 是不够的：原子替换只保证"不写半截"，**不保证不覆盖别人的更新**
      —— 两个实例同时 rebuild，后者会把前者刚写的内容整体盖掉，而且**双方都不报错**。
      这是"同一 inode 整体重写互覆"，只有"读—改—写"三段式的冲突检测能挡住它。

    ★`before=None` 时保持原子写（用于 sidecar 另存等"不基于旧内容"的写入）。
    ★`HG is None` 时仍是裸写 —— 调用方（rebuild）负责在此之前 fail-closed。
    """
    if HG is not None:
        if before is not None:
            return HG.commit_guarded(path, text, before,
                                     on_conflict=on_conflict, tag=tag)
        # 2026-11-07 (Persome P1#9): projections go through the two-phase
        # transactional write (journal fsynced BEFORE the replace) so a crash
        # mid-rebuild leaves an auditable intent record instead of silence.
        return HG.transactional_write(path, text, op='projection.' + (tag or 'write'))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return {'ok': True, 'path': path, 'bytes': os.path.getsize(path), 'atomic': False}


HUB = os.path.dirname(os.path.abspath(__file__))
# ★真源库路径可被 MEM_DB 覆盖（默认仍是 HUB/memory.db，不设环境变量时行为完全不变）。
#   用途：仓库不发布真实 memory.db（含真名/学号/本机路径/密钥，见 .gitignore），
#   但开源版需要「clone 下来就能跑」——指向合成演示库即可：
#       MEM_DB=demo/memory_demo.db memtether search "演示查询"
#   演示库由 scripts/make_demo_db.py 生成（固定随机种子，逐字节可复现，零真实串）。
# P4 (2026-10-05): single source of truth for default DB path.
# Prior state had THREE divergent defaults (CLI ~/.memtether, engine package-dir,
# memtether_paths LOCALAPPDATA). Every entry point must resolve identically.
try:
    import memtether_paths as _mp
    DB = os.environ.get('MEM_DB') or _mp.default_db()
except Exception:
    DB = os.environ.get('MEM_DB') or os.path.join(HUB, 'memory.db')
if not os.path.isabs(DB):
    DB = os.path.join(HUB, DB)
# ★sink.json 同样可被 MEM_SINK_PATH 覆盖（2026-09-17 加，理由与 MEM_DB 完全一样）：
#   sink.json 落在**仓库内**，而 rebuild 每次都会把它**整体重写**。
#   用 MEM_DB 指向隔离库做演练时，若不隔离 sink.json，就会把仓库里那份真实导出
#   （实测 354945 B）整体覆盖成演练内容 —— 又是"跑起来不报错、但结果错"。
#   ★不设该变量时路径与原来**逐字节相同**（HUB/sink.json），行为不变。
SINK = os.environ.get('MEM_SINK_PATH') or os.path.join(HUB, 'sink.json')

# ---- 来源名校验（2026-09-20 从真源移植）-------------------------------------
# 为什么需要：写入入口原先对 source **不做任何校验** —— 脚本把自己的名字
# （如 'tool_audit.py 2026-09-15'）当来源传进来会被照单全收，归属被写花，
# 而且没人会注意到（与"静默覆盖"同族：出错时不报错）。
# 校验口径与 mem.py 的 known_sources 同源，都读 <HUB>/agents.json。
_NEUTRAL_SOURCES = ('local', 'unknown')


def _known_sources():
    """已注册的来源名（读 agents.json）。读不到返回 []（= 不校验，放行）。"""
    f = os.path.join(HUB, 'agents.json')
    try:
        with open(f, 'r', encoding='utf-8') as fh:
            reg = json.load(fh)
    except Exception:
        return []
    out = []
    for k, v in (reg.get('agents') or {}).items():
        out.append(k)
        for a in ((v or {}).get('aliases') or []):
            out.append(a)
    return out


def _guard_source(source, fallback=None):
    """写入路径的来源名必须已注册，防止脚本/agent 自造名把归属搞乱。

    · 空值归一为 'unknown'。
    · 读不到 agents.json 时**放行**（fail-open）—— 不能让"配置缺失"变成"写不进记忆"。
      这是开源版的主要路径：发布集不含 agents.json，陌生人装完即可用。
    · 中性默认值（local / unknown）永远放行 —— 它们是"未识别来源"的诚实标记，
      不该因为没被登记而拒绝写入。
    · 应急放行：环境变量 MEM_SOURCE_GUARD=0。
    · ★软着陆：未注册时若给了 fallback（参数或 MEM_SOURCE_FALLBACK，且该值本身
      已注册），回退到它并记 warning，**不再 raise SystemExit**。原因：SystemExit
      不被包装层的 `except Exception` 捕获 ⇒ 长驻进程（MCP server）会**直接死掉**
      （服务静默消失，比报错更糟）。CLI 场景不给 fallback，行为与硬报错一致。
    """
    src = (source or '').strip() or 'unknown'
    if os.environ.get('MEM_SOURCE_GUARD', '1') == '0':
        return src
    if src in _NEUTRAL_SOURCES:
        return src
    reg = _known_sources()
    if reg and src not in reg:
        fb = (fallback or os.environ.get('MEM_SOURCE_FALLBACK') or '').strip()
        if fb and fb in reg:
            try:
                sys.stderr.write(
                    '[gateway] WARN: 未注册来源 %r，软着陆回退为 %r（避免长驻进程退出）\n'
                    % (src, fb))
            except Exception:
                pass
            return fb
        raise SystemExit(
            'ERROR: 未注册的来源 %r（gateway.py 写入路径校验）。\n'
            '已注册: %s\n'
            '如需新增，编辑 %s\n'
            '应急放行可设 MEM_SOURCE_GUARD=0' %
            (src, ', '.join(sorted(set(reg))), os.path.join(HUB, 'agents.json')))
    return src



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
  updated_at TEXT,
  q_value REAL DEFAULT 0.5,   -- ★升级 1：被采纳的价值分（0~1，中性 0.5）
  use_count INTEGER DEFAULT 0,-- ★升级 1：被采纳次数（纯计数，不参与打分）
  last_verified_at TEXT       -- ★2026-11-04: verify_at renewal timestamp for facts
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
  updated_at TEXT,
  q_value REAL DEFAULT 0.5,   -- ★升级 1 扩展（2026-09-20）：资产也有价值分，
  use_count INTEGER DEFAULT 0 --   此前只 facts 有，资产占检索结果近半却吃不到加权
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
CREATE TABLE IF NOT EXISTS fact_entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity TEXT NOT NULL,
  attr TEXT,
  value TEXT,
  fact_uid TEXT NOT NULL,
  source TEXT,
  ts TEXT,
  valid_from TEXT,
  valid_to TEXT
);
CREATE INDEX IF NOT EXISTS idx_fe_entity ON fact_entities(entity);
CREATE INDEX IF NOT EXISTS idx_fe_entity_attr ON fact_entities(entity, attr);

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


def _tenant_filter():
    """P6: tenant isolation at query layer. Reads MEM_TENANT_ID from env.
    P0-1 (2026-10-06): returns (sql_fragment, param_value) for parameterized
    query — was string-concatenation (SQL injection via semicolons).
    Callers: .execute(sql, params) where params includes the tid.
    """
    tid = os.environ.get('MEM_TENANT_ID', 'default')
    if tid == 'default':
        return '', None
    return ' AND tenant_id = ?', tid


def get_conn():
    # 2026-10-22: align with memory_hub. The published build used to open the
    # DB without WAL and without a busy timeout, so the moment two clients
    # (or a client plus the reflector) touched it, one of them got
    # "database is locked". WAL lets readers and a writer proceed together;
    # busy_timeout makes the loser wait instead of failing.
    # Wrapped in try/except because some filesystems (network shares) refuse
    # WAL - degrading to the old behaviour beats refusing to open.
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=5000')
    except Exception:
        pass
    # 2026-11-07 (Persome P1#6): corruption probe — Persome's connect() runs a
    # canary SELECT on every connection. SQLite happily hands out a connection
    # to a corrupted db and only fails later mid-transaction; a probe here
    # converts "works for a while then explodes" into "fails at connect time".
    try:
        conn.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone()
    except Exception as _probe_err:
        conn.close()
        raise RuntimeError(
            'memory.db corrupted or unreadable (probed at connect): '
            f'{type(_probe_err).__name__}: {str(_probe_err)[:120]}'
        ) from _probe_err
    return conn


def _ensure_columns(conn):
    # P6 (2026-10-05): multi-tenant support
    try:
        conn.execute("ALTER TABLE facts ADD COLUMN tenant_id TEXT DEFAULT 'default'")
    except Exception:
        pass  # column already exists
    """★升级 1：给**已存在的旧库**补 q_value / use_count 两列。

    为什么必须有：SCHEMA 用的是 `CREATE TABLE IF NOT EXISTS` —— 新库能拿到新列，
    **旧库拿不到**（表已存在，整段 CREATE 被跳过）。而 memsearch 的候选池查询
    会显式 SELECT q_value，旧库上会直接抛异常 ⇒ 检索整体降级。
    这类"跑起来不报错、但结果全错/功能静默失效"正是本项目反复踩的坑，
    故在这里做**幂等自动迁移**，用户升级后无需手跑任何脚本。

    只做 `ALTER TABLE ADD COLUMN`（SQLite 元数据级、毫秒级、全部常量默认值），
    不动任何既有行内容；列已存在则跳过。等价的手动工具见 migrate_qvalue.py。
    """
    try:
        have = {r[1] for r in conn.execute('PRAGMA table_info(facts)')}
    except sqlite3.Error:
        return
    # P0 (2026-11-03): verify_at must be self-healing too — release repo added
    # the column for staleness tracking, but CREATE TABLE IF NOT EXISTS never
    # upgrades an existing database. Without this, older installs break on the
    # first remember() with "table facts has no column named verify_at".
    # 2026-11-04 competitor-audit fix: a brand-new DB had verify_at but NOT
    # last_verified_at, so memsearch's renewal UPDATE raised "no such column"
    # and its bare except made the whole freshness mechanism a silent no-op.
    # Production DBs had the column added manually, hiding the gap in testing.
    for name, typ, dflt in (('q_value', 'REAL', '0.5'), ('use_count', 'INTEGER', '0'),
                            ('predicate', 'TEXT', None), ('verify_at', 'TEXT', None),
                    ('expires_at', 'TEXT', None),
                            ('last_verified_at', 'TEXT', None)):
        if name in have:
            continue
        try:
            conn.execute('ALTER TABLE facts ADD COLUMN %s %s%s' % (name, typ, (' DEFAULT ' + dflt) if dflt else ''))
        except sqlite3.OperationalError as e:
            if 'duplicate column' not in str(e).lower():
                raise
            # a47: two processes ran init_db concurrently (multi-client startup
            # on a shared db is the product's core scenario) — the other process
            # added the column between our PRAGMA and our ALTER. Harmless.
    # ★2026-09-20 扩展：tool_assets 也补 —— 资产条目占检索结果近半，
    #   没有这两列就永远吃不到 Q-Value 加权（bump 也无对象）。
    try:
        have_a = {r[1] for r in conn.execute('PRAGMA table_info(tool_assets)')}
    except sqlite3.Error:
        return
    for name, typ, dflt in (('q_value', 'REAL', '0.5'), ('use_count', 'INTEGER', '0')):
        if name in have_a:
            continue
        try:
            conn.execute('ALTER TABLE tool_assets ADD COLUMN %s %s DEFAULT %s'
                         % (name, typ, dflt))
        except sqlite3.OperationalError as e:
            if 'duplicate column' not in str(e).lower():
                raise
    # 2026-10-21: fact_entities.entity_name self-heal.
    # The production hub still has this NOT NULL column from an older schema
    # while our SCHEMA does not define it; CREATE TABLE IF NOT EXISTS never adds
    # columns, so writing into a hub-shaped DB raised
    # "NOT NULL constraint failed: fact_entities.entity_name".
    try:
        have_e = {r[1] for r in conn.execute('PRAGMA table_info(fact_entities)')}
        if 'entity_name' not in have_e:
            conn.execute("ALTER TABLE fact_entities ADD COLUMN entity_name TEXT DEFAULT 'legacy'")
    except sqlite3.OperationalError:
        pass


def init_db():
    conn = get_conn()
    # P7 (2026-10-05): multi-client pitch requires WAL + sane busy_timeout.
    # README/SECURITY claimed WAL; reality was journal_mode=delete with a 5s
    # busy timeout — concurrent readers hit "database is locked" under any
    # sustained write. WAL is persistent; busy_timeout is per-connection.
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=8000')
    except Exception:
        pass
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    conn.commit()
    conn.close()

    _init_fts_triggers()  # FTS + triggers
    _verify_schema_fingerprint()


def _schema_fingerprint(conn=None):
    """Stable hash over (table -> sorted column names). Persome P1#7:
    detects column drift that CREATE-IF-NOT-EXISTS + idempotent ALTERs can
    both silently miss (e.g. a hand-edited or older db with renamed cols)."""
    import hashlib as _hl
    _own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        parts = []
        for (tname,) in rows:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info({tname})')]
            parts.append(tname + ':' + ','.join(sorted(cols)))
        return _hl.sha256(('|'.join(parts)).encode('utf-8')).hexdigest()[:16]
    finally:
        if _own:
            conn.close()


def _verify_schema_fingerprint():
    """Warn-once (not raise): fingerprint changes are expected across versions,
    so a hard raise would brick every legitimate upgrade. But drift must be
    VISIBLE — silent divergence is exactly the Persome failure class."""
    global _schema_fp_warned
    try:
        fp = _schema_fingerprint()
    except Exception as e:
        sys.stderr.write(f'[memtether] schema fingerprint unavailable: {e}\n')
        return
    expected = os.environ.get('MEM_SCHEMA_FP')
    if expected and fp != expected:
        if not _schema_fp_warned:
            sys.stderr.write(
                '[memtether] SCHEMA DRIFT: fingerprint ' + fp +
                ' != expected ' + expected + ' (set MEM_SCHEMA_FP to silence)\n')
            _schema_fp_warned = True


def _init_fts_triggers():
    """Initialize FTS5 table and triggers (SQLite-level sync, no Python try/except)."""
    conn = get_conn()
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(uid UNINDEXED, content, tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS facts_fts_insert AFTER INSERT ON facts BEGIN INSERT INTO facts_fts(uid, content) VALUES (NEW.uid, NEW.content); END;
        CREATE TRIGGER IF NOT EXISTS facts_fts_delete AFTER DELETE ON facts BEGIN DELETE FROM facts_fts WHERE uid = OLD.uid; END;
        CREATE TRIGGER IF NOT EXISTS facts_fts_retire AFTER UPDATE OF status ON facts WHEN NEW.status != 'active' BEGIN DELETE FROM facts_fts WHERE uid = NEW.uid; END;
        -- 2026-11-04: resurrect path. retire removes from FTS, but nothing
        -- re-added rows that went back to active (found via the nrem rollback:
        -- 40 restored rows were invisible to full-text search until repaired).
        CREATE TRIGGER IF NOT EXISTS facts_fts_resurrect AFTER UPDATE OF status ON facts WHEN NEW.status = 'active' BEGIN
            INSERT INTO facts_fts(uid, content)
            SELECT NEW.uid, NEW.content
            WHERE NOT EXISTS (SELECT 1 FROM facts_fts WHERE uid = NEW.uid);
        END;
    """)
    conn.commit()
    conn.close()


def audit(conn, op, target, agent, detail=''):
    conn.execute('INSERT INTO audit_log (op,target,agent,detail,ts) VALUES (?,?,?,?,?)',
                 (op, target, agent, detail[:500], now()))
    # a74: auto-anchor the audit hash chain every BATCH writes. Previously
    # anchoring only happened via explicit court CLI / api_server, so CLI+MCP
    # writes left an unanchored tail = silent tamper window. PoisonRecall-style
    # erasure attacks exploit exactly that gap.
    try:
        import memory_court as _mc
        _mc.maybe_anchor(conn)
    except Exception as _anch_err:
        # anchoring must never block writes; api/court still anchor on demand.
        # a75-r3: fail-SILENT is itself a tamper-detection hole — warn once per
        # process so operators notice the chain stopped being maintained.
        global _anchor_warned
        try:
            if not _anchor_warned:
                sys.stderr.write(
                    '[memtether] audit auto-anchor FAILED (chain not maintained): '
                    f'{type(_anch_err).__name__}: {str(_anch_err)[:120]}\n')
                _anchor_warned = True
        except Exception:
            pass


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


def _vec_upsert(uid, content, type_='fact', source=DEFAULT_SOURCE):
    """把事实同步进 ChromaDB facts_active 向量索引。失败不阻塞。

    FIX 10 (2026-10-14): skip if rebuild lock is held to avoid racing
    with delete_collection. Skipped entries caught by consistency gate.
    """
    try:
        # ★2026-10-19 修正（实测 stale 锁事故，与 hub 同源）：
        #   原判据是 `os.path.exists()`，而 rebuild 若被硬杀则 unlink 永不执行，
        #   留下**没人持锁**的 stale 文件。实测后果：该文件存在 >1 天，
        #   `_vec_upsert` 全部走 skip 分支，新写入的记忆**静默不进向量索引**；
        #   而 consistency gate 的 drift < tolerance 判定"没问题"，不触发 rebuild。
        #   正确判据是「能否拿到 msvcrt 锁」—— 锁是内核级状态，进程死了自动释放。
        _gate_lock = os.path.join(HUB, '.gate_rebuild.lock')
        _held = False
        try:
            import msvcrt as _ms
            _fd = os.open(_gate_lock, os.O_CREAT | os.O_RDWR)
            try:
                _ms.locking(_fd, _ms.LK_NBLCK, 1)
                _ms.locking(_fd, _ms.LK_UNLCK, 1)   # 探测完立即释放
            finally:
                os.close(_fd)
        except OSError:
            _held = True     # 抢不到锁 = 真有人在重建
        except Exception:
            _held = os.path.exists(_gate_lock)   # 非 Windows 兜底：退回文件探测
        if _held:
            sys.stderr.write(
                '[vec_upsert] rebuild in progress, skipping uid=%s\n' % uid[:28])
            return {'skipped': 'rebuild_in_progress'}
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
    except ModuleNotFoundError as e:
        # chromadb/numpy not installed -> graceful degradation, don't scare strangers
        return {'skipped': 'no_vector'}
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
    except ModuleNotFoundError as e:
        return {'skipped': 'no_vector'}
    except Exception as e:
        return {'error': str(e)[:120]}


# ★P4 (2026-11-03): Incident cooldown helpers.
# Kept outside remember() so they are unit-testable and so the cooldown logic
# can run before the generic dedup paths without duplicating SQL blocks.
_INCIDENT_COOLDOWN_WINDOW_H = int(os.environ.get('MEM_INCIDENT_COOLDOWN_H', '24'))
_INCIDENT_FATAL_KEYWORDS = (
    '数据丢失', '删库', 'security', '泄露', 'corrupt', 'security incident',
    '写入失败', '写入通道', 'integrityerror', 'not null constraint',
    'fatal incident',
)


def _is_fatal_incident(content):
    """Fatal incidents keep type=incident; everything else is experience noise."""
    c = (content or '').lower()
    return any(kw in c for kw in _INCIDENT_FATAL_KEYWORDS)


def _incident_cooldown(conn, content, source, ts):
    """Collapse a same-signature incident retry into its existing row.

    Returns the cooldown result dict, or None when this is a new incident.
    The 24h window starts from *now* (not local midnight): a retry at 23:50
    and another at 00:05 are still the same operational event.
    """
    import datetime as _dt
    _sig = (content or '').strip()[:40]
    if not _sig:
        return None
    _cutoff = (_dt.datetime.now() - _dt.timedelta(hours=_INCIDENT_COOLDOWN_WINDOW_H))\
        .strftime('%Y-%m-%d %H:%M:%S')
    _recent = conn.execute(
        "SELECT uid, tags FROM facts WHERE type='incident' AND status='active' "
        "AND substr(content, 1, 40)=? AND created_at > ? LIMIT 1",
        (_sig, _cutoff)).fetchone()
    if not _recent:
        return None
    _old_tags = _recent['tags'] or ''
    _count = 1
    for _t in _old_tags.split():
        if _t.startswith('hit:'):
            try:
                _count = int(_t[4:]) + 1
            except ValueError:
                _count = 2
    _new_tags = ' '.join(t for t in _old_tags.split() if not t.startswith('hit:'))
    _new_tags = (_new_tags + ' ' if _new_tags else '') + 'hit:%d' % _count
    conn.execute("UPDATE facts SET tags=?, updated_at=? WHERE uid=?",
                 (_new_tags, ts, _recent['uid']))
    audit(conn, 'incident_cooldown', _recent['uid'], source,
          'cooldown hit:%d sig=%s' % (_count, _sig[:30]))
    conn.commit()
    return {'ok': True, 'uid': _recent['uid'], 'op': 'cooldown_increment',
            'count': _count, 'vec': {'skipped': 'cooldown'}}


def remember(content, type='fact', source=DEFAULT_SOURCE, scope='shared', subject='user',
             confidence=0.8, tags='', status='active', mem0=False, valid_from=None):
    # --- Input validation (security hardening) ---
    if not content or not content.strip():
        raise ValueError("content must be non-empty")
    if len(content) > 10240:
        raise ValueError(f"content too long: {len(content)} chars (max 10240)")
    _VALID_TYPES = ('fact', 'experience', 'decision', 'incident', 'todo', 'procedure', 'preference', 'environment')
    if type not in _VALID_TYPES:
        raise ValueError(f"type must be one of {_VALID_TYPES}, got: {type}")
    if not source or len(source) > 64:
        raise ValueError(f"source must be 1-64 chars, got: {repr(source)}")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be 0.0-1.0, got: {confidence}")
    if scope not in ('shared', 'private', 'restricted'):
        raise ValueError(f"scope must be shared/private/restricted, got: {scope}")
    """写入/更新一条事实。若内容高度相似则更新，若冲突则 supersede。
    可选 mem0=True 时同步写入 Mem0 语义索引（自动提取+冲突消解）。

    valid_from: T 轴（有效时间）起点。默认 None → 取当前时刻，即假定"记录时即成立"。
      事后复盘类事实应**显式传入**：例如本库真实条目
      "通知问题定案(2026-09-11)" 是 09-13 才记录的，
      其 valid_from 应为 2026-09-11、recorded_at 为 09-13。
      ★这正是双时间轴存在的意义——让"事实何时成立"与"系统何时知道"分离，
        否则查"09-12 系统认为什么为真"会得出错误结论。
    """
    source = _guard_source(source)
    # 2026-11-06 (Persome audit P0#1): remember() previously bypassed
    # the sensitive gate entirely - only correct()/record_tool() checked.
    # An email/phone/JWT typed into a plain remember() went straight in.
    if _SENSITIVE_BLOCK:
        _should_block, _blocked_labels, _redacted, _all_hits = _sensitive_check_with_policy(content)
        if _should_block:
            raise ValueError(
                '[P0#1] 敏感内容闸门(remember)：内容含完整凭据 %s，拒绝入库。'
                % ', '.join(_blocked_labels))
        if _redacted != content:
            content = _redacted
    # ★2026-11-03: auto verify_at for env facts (paths/exes/versions) -> 30d staleness
    try:
        if type in ('fact', 'environment') and (
                re.search(r'[A-Z]:[/\\]', content)
                or re.search(r'\.(exe|py|bat|ps1|db|json)\b', content)
                or re.search(r'\bv?\d+\.\d+\.\d+\b', content)):
            from datetime import datetime, timedelta
            _vf = valid_from if valid_from else now()
            _verify = (datetime.strptime(_vf[:10], '%Y-%m-%d') + timedelta(days=30)).strftime('%Y-%m-%d')
            if _verify not in (tags or ''):
                tags = (tags + ' ' if tags else '') + 'verify:' + _verify
    except Exception:
        pass
    # ★P0-07 (2026-09-24)：写入侧 TTL 提醒——状态类结论（“当前/最新/余额/可用…”）
    #   会随时间失效，却没有复核截止日 → 后继会话会把旧结论当当前答案（已实际发生）。
    #   ★刻意只 warning 不拒写：拒写会打断生产写入；提示到 stderr 足以让人补 ttl。
    try:
        import memsearch as _ms
        if _ms.looks_like_state(content) and not _ms._parse_ttl(tags, None, content):
            sys.stderr.write(
                '[P0-07] 疑似状态类结论但无 TTL：建议在 tags 或正文写 ttl:YYYY-MM-DD，'
                '否则过期后会被降权并标注「可能不是当前状态」。\n')
    except Exception:
        pass
    init_db()
    conn = get_conn()
    try:
        uid = _uid('fact', content + source)
        # ★P4-fix (2026-11-03): incident cooldown must run BEFORE all dedup
        # gates. Exact dup and near-dup both hide retries behind noop_dup /
        # noop_near_dup, so a cooldown placed after dedup can never fire and
        # repeated real-world incidents silently lose their hit counters.
        if type == 'incident':
            _cooldown = _incident_cooldown(conn, content, source, now())
            if _cooldown is not None:
                return _cooldown
        # 查重：相同 source + 高度相似内容
        existing = conn.execute(
            "SELECT uid FROM facts WHERE source=? AND status='active' AND content=?",
            (source, content)).fetchone()
        if existing:
            conn.execute("UPDATE facts SET updated_at=? WHERE uid=?", (now(), existing['uid']))
            audit(conn, 'remember_dup', existing['uid'], source, content[:80])
            conn.commit()
            return {'ok': True, 'uid': existing['uid'], 'op': 'noop_dup'}

        # W1.6-5 (2026-11-02): cross-source feedback-loop gate.
        # A memory recalled by one agent and re-ingested under a DIFFERENT
        # source must not create a second active row (memoripy calls this
        # "refusing to re-ingest retrieved memory as fresh evidence").
        # Exact content match across sources → attribute to the ORIGINAL
        # row, note the echo, do not insert.
        echo = conn.execute(
            "SELECT uid, source FROM facts WHERE status='active' AND content=? AND source<>? LIMIT 1",
            (content, source)).fetchone()
        if echo:
            conn.execute("UPDATE facts SET updated_at=? WHERE uid=?", (now(), echo['uid']))
            audit(conn, 'remember_echo', echo['uid'], source,
                  'cross-source echo of %s' % echo['source'])
            conn.commit()
            return {'ok': True, 'uid': echo['uid'], 'op': 'noop_echo',
                    'original_source': echo['source']}

        # 近义去重（2026-09-13）：向量相似度 + 文本相似度双确认，视为同一事实
        try:
            sys.path.insert(0, HUB)
            import memsearch
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
                # T1 fix (2026-10-13): near-dup only within same source.
                _cand_meta = (got.get('metadatas') or [{}])[0] if got.get('metadatas') else {}
                _cand_src = (_cand_meta.get('source') or '') if isinstance(_cand_meta, dict) else ''
                if _cand_src and source and _cand_src != source:
                    # W1.6-5 (2026-11-04): cross-source paraphrase feedback loop.
                    # Previously we just skipped — a re-ingested paraphrase from a
                    # different agent created a duplicate row. Now we flag it:
                    # attribute to the ORIGINAL row (echo semantics), audit it,
                    # and return without inserting. The caller sees op='feedback_loop'
                    # and knows this was a re-ingestion, not new information.
                    _fb_tags = 'feedback_loop'
                    conn.execute(
                        "UPDATE facts SET updated_at=?, tags=CASE WHEN tags IS NULL OR tags='' "
                        "THEN ? ELSE tags || ' ' || ? END WHERE uid=?",
                        (now(), _fb_tags, _fb_tags, did))
                    audit(conn, 'feedback_loop_cross_source', did, source,
                          'cross-source paraphrase re-ingestion (orig_src=%s sim=%.3f)'
                          % (_cand_src, sim))
                    conn.commit()
                    return {'ok': True, 'uid': did, 'op': 'feedback_loop',
                            'original_source': _cand_src, 'sim': round(sim, 3),
                            'note': 'cross-source paraphrase caught by feedback-loop gate'}
                # P1-2 (2026-10-05): ghost-vector guard.
                # Vector store can outlive its facts DB (test isolation,
                # supersede race). If uid is not an active row in THIS facts
                # DB, the hit is a ghost - purge it and write the new fact.
                row_ok = conn.execute(
                "SELECT 1 FROM facts WHERE uid=? AND status='active'", (did,)).fetchone()
                if not row_ok:
                    try:
                        col.delete(ids=[did])
                    except Exception:
                        pass
                    continue
                doc = got['documents'][0]
                if True:  # a50: embed sim >= 0.92 IS the dedup criterion (SequenceMatcher fails on CJK paraphrases)
                    conn.execute("UPDATE facts SET updated_at=? WHERE uid=?", (now(), did))
                    audit(conn, 'remember_near_dup', did, source, content[:60])
                    conn.commit()
                    return {'ok': True, 'uid': did, 'op': 'noop_near_dup',
                            'sim': round(sim, 3)}
        except Exception:
            pass

        ts = now()
        # 2026-11-03: extract verify_at from tags for column value
        _vat = None
        if tags:
            for _t in tags.split():
                if _t.startswith('verify:'):
                    _vat = _t[7:]
                    break
        # 2026-11-07 (mem0 P0): extract expires: from tags — HARD expiry date.
        # verify_at = "may be stale, check me"; expires: = "dead after date, hide me".
        _expires = None
        if tags:
            for _t in tags.split():
                if _t.startswith('expires:'):
                    _expires = _t[8:]
                    break

        # ★P4 (2026-11-03): Incident severity tiering.
        # Cooldown matching already ran before dedup; only newly inserted
        # incidents reach this point. Non-fatal incidents are downgraded to
        # experience so they stop crowding out genuinely fatal incident rows.
        if type == 'incident':
            if not _is_fatal_incident(content):
                type = 'experience'

        conn.execute(
            """INSERT INTO facts (uid,type,subject,content,status,source,scope,confidence,tags,
                                  created_at,updated_at,valid_from,recorded_at,temporal_source,tenant_id,verify_at,expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, type, subject, content, status, source, scope, confidence, tags,
             ts, ts, valid_from or ts, ts, 'native', os.environ.get('MEM_TENANT_ID', 'default'), _vat, _expires))
        # ★十三修：写入时自动抽取谓词（entity→attr 对）存入 predicate 列
        try:
            from predicates import store_predicate, store_entities
            store_predicate(conn, uid, content)
            # SGM Phase 1: also write structured entities for SQL-deterministic queries
            store_entities(conn, uid, content, source=source, ts=now())
        except ImportError:
            pass  # predicates.py 不存在时降级为旧行为
        audit(conn, 'remember', uid, source, content[:80])
        conn.commit()

        # 可选 Mem0 同步
        m0 = None
        if mem0:
            m0 = _mem0_add(content)

        # 自动同步向量索引（2026-09-13：消除"新记忆需手动 rebuild_index"缺口）
        # P-TB2 (2026-10-05): skip vector sync during bulk import (env-controlled)
        if os.environ.get('MEM_SKIP_VECTOR'):
            vec = {'skipped': 'bulk_import'}
        else:
            vec = _vec_upsert(uid, content, type, source)

        return {'ok': True, 'uid': uid, 'op': 'insert', 'mem0': m0, 'vec': vec}
    finally:
        conn.close()


# FIX (2026-10-14): _SENSITIVE_PATTERNS/_SENSITIVE_BLOCK/_sensitive_check
# were missing from this file (existed only in memory_hub gateway.py).
# Adding them here to fix NameError when correct()/record_tool() is called.
_SENSITIVE_PATTERNS = [
    # --- original 6 (provider key prefixes) ---
    (re.compile(r'sk-[A-Za-z0-9_\-]{20,}'), 'API key'),
    (re.compile(r'ghp_[A-Za-z0-9]{36,}'), 'GitHub token'),
    (re.compile(r'gho_[A-Za-z0-9]{36,}'), 'GitHub OAuth token'),
    (re.compile(r'AKIA[0-9A-Z]{16}'), 'AWS access key'),
    (re.compile(r'glpat-[A-Za-z0-9\-]{20,}'), 'GitLab token'),
    (re.compile(r'xoxb-[0-9A-Za-z\-]{20,}'), 'Slack bot token'),
    # --- 2026-11-06 (Persome privacy/scrub.py P0#1): 6 more classes ---
    # Bearer / Authorization tokens (case-insensitive)
    (re.compile(r'(?i)\b(?:bearer|authorization)\s*[:=]?\s+[A-Za-z0-9._\-]{16,}'), 'Bearer token'),
    # JWT (three base64url segments)
    (re.compile(r'\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}'), 'JWT'),
    # password= / password: (en + zh), word boundary to avoid matching in prose
    (re.compile(r'(?i)\b(?:password|passwd|pwd|secret|api[_\-]?key)\b\s*[:=]\s*\S+'), 'Password assignment'),
    (re.compile(r'(?:\u5bc6\u7801|\u53e3\u4ee4|\u5bc6\u94a5)\s*[:\uff1a=]\s*\S+'), 'Password assignment (zh)'),
    # Mainland-CN mobile (11 digits, not part of longer run)
    (re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'), 'CN mobile number'),
    # Email
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'), 'Email'),
    # --- 2026-11-07 (Persome parity, P0 remainder): 5 more classes ---
    # Google API key (AIza + 35 alnum)
    (re.compile(r'\bAIza[0-9A-Za-z\-_]{35}\b'), 'Google API key'),
    # International phone: + followed by 7-15 digits, not part of longer run
    (re.compile(r'(?<![\dA-Za-z])\+\d{7,15}(?!\d)'), 'International phone'),
    # Card / national ID: 13-19 digit run (BN boundaries; pure digits too broad
    # alone -> require checksum-ish shape: no separators, common length window)
    (re.compile(r'(?<!\d)\d{13,19}(?!\d)'), 'Card or ID number'),
    # Home path exposure: /Users/<name> or /home/<name> (Persome #398: no
    # trailing slash requirement, username segment excludes separators)
    (re.compile(r'(?i)(?:/Users/|/home/)[A-Za-z0-9_.\- ]{1,40}(?=/|\s|$)'), 'Home path'),
    # High-entropy string: 32+ mixed base64/hex (Persome #389-again: exclude
    # - and _ from the class so package names never match)
    (re.compile(r'\b(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{32,}\b'), 'High-entropy string'),
]
_SENSITIVE_BLOCK = os.environ.get('MEM_SENSITIVE_GATE', '1') != '0'


# ============================================================================
# 2026-11-07 (Hindsight memory_defense.py): configurable PII policy engine
# Each pattern gets an action: "block" (reject write), "redact" (strip match,
# write [REDACTED:type]), "allow" (log but pass through). Default: block.
# Env MEM_PII_POLICY={"Email":"redact","High-entropy string":"allow"} overrides.
# ============================================================================

def _parse_pii_policy():
    """Parse MEM_PII_POLICY env into {pattern_label: action_str}."""
    import json as _json
    _raw = os.environ.get('MEM_PII_POLICY', '')
    if not _raw:
        return {}
    try:
        _d = _json.loads(_raw)
        return {str(k): str(v).lower() for k, v in _d.items()
                if str(v).lower() in ('block', 'redact', 'allow')}
    except Exception:
        return {}

def _pii_action_for(label, policy=None):
    """Get action for a pattern label: policy override or default 'block'."""
    if policy is None:
        policy = _parse_pii_policy()
    return policy.get(label, 'block')

def _fingerprint_value(value):
    """Redaction-identifiable preview of a matched value (Hindsight style)."""
    n = len(value)
    if n < 6:
        return '[redacted]'
    if n <= 15:
        return f'{value[:2]}...{value[-2:]}'
    return f'{value[:4]}...{value[-4:]}'

def _sensitive_check_with_policy(content):
    """Run _SENSITIVE_PATTERNS with per-pattern actions.

    Returns (should_block, blocked_labels, redacted_content, all_hits).
    """
    if not _SENSITIVE_BLOCK:
        return (False, [], content, [])

    policy = _parse_pii_policy()
    blocked = []
    hits = []
    redacted = content
    for pat, label in _SENSITIVE_PATTERNS:
        action = _pii_action_for(label, policy)
        matches = pat.findall(content)
        if not matches:
            continue
        for m in matches[:5]:
            val = m if isinstance(m, str) else m[0] if m else ''
            hits.append({'detector': label, 'preview': _fingerprint_value(val),
                         'action': action})
        if action == 'block':
            blocked.append(label)
        elif action == 'redact':
            redacted = pat.sub(f'[REDACTED:{label}]', redacted)

    return (len(blocked) > 0, blocked, redacted, hits)

def _sensitive_check(content):
    hits = []
    for pat, label in _SENSITIVE_PATTERNS:
        m = pat.search(content)
        if m:
            hits.append((label, m.group(0)[:8] + '...'))
    return hits


def correct(old_uid, new_content, reason, by_agent=DEFAULT_SOURCE, valid_from=None):
    """用户纠正：旧事实 supersede，新事实 active。

    双时间轴处理：
      旧事实 valid_to = 新事实 valid_from（T 轴连续——旧事实止于新事实起，
        不留下"两边都不成立"的时间空档）
      旧事实 invalidated_at = 当前时刻（T' 轴——**我们此刻**才判定它失效，
        可能与 valid_to 不同；若纠正的是陈年旧事，两个值会明显分离）
    """
    by_agent = _guard_source(by_agent)

    # F7 fix (2026-10-13): P0-03 sensitive gate — correct() previously
    # bypassed _sensitive_check, letting full credentials in via new_content.
    if _SENSITIVE_BLOCK:
        _should_block_c, _blocked_c, _redacted_c, _hits_c = _sensitive_check_with_policy(new_content)
        if _should_block_c:
            raise ValueError(
                '[P0-03] 敏感内容闸门(correct)：新内容含完整凭据 %s，拒绝入库。'
                % ', '.join(_blocked_c))
        if _redacted_c != new_content:
            new_content = _redacted_c
    init_db()
    conn = get_conn()
    try:
        old = conn.execute("SELECT * FROM facts WHERE uid=?", (old_uid,)).fetchone()
        if not old:
            return {'ok': False, 'error': 'old uid not found: %s' % old_uid}
        if old['status'] not in ('active',):
            return {'ok': False, 'error': 'old uid is not active (status=%s, cannot correct a dead entry)' % old['status'],
                    'uid': old_uid, 'status': old['status']}
        new_uid = _uid('fact', new_content + by_agent)
        ts = now()
        nvf = valid_from or ts          # 新事实的 T 轴起点
        # 旧事实标记 superseded
        # ★2026-09-24：旧行 supersede 后清 pin（同 memory_hub）
        # a75-r6: cycle detection - a supersession cycle makes the version
        # chain unresolvable (multi-hop expansion walks at most 10 hops then
        # silently stops). Refuse if old_uid is reachable from new_uid.
        _seen = set([old_uid])
        _cur = new_uid
        _cycle = False
        while _cur:
            if _cur in _seen:
                _cycle = True
                break
            _seen.add(_cur)
            _row = conn.execute(
                "SELECT superseded_by FROM facts WHERE uid=?",
                (_cur,)).fetchone()
            _cur = _row[0] if _row else None
        if _cycle:
            conn.rollback()
            return {"ok": False,
                    "error": ("supersession cycle refused: %s already "
                              "transitively supersedes %s"
                              % (new_uid, old_uid))}
        # a75-r6: race guard - only supersede a fact that is still active.
        # Two concurrent correct() calls previously forked the chain (two
        # supersessions edges pointing to two active children).
        conn.execute("UPDATE facts SET status='superseded', superseded_by=?, valid_to=?, "
                     "invalidated_at=?, updated_at=?, tags=(CASE WHEN (',' || lower(REPLACE(COALESCE(tags,''), ' ', ',')) || ',') LIKE '%,pin,%' "
                     "THEN trim(replace(replace(',' || tags || ',', ',pin,', ','), ',PIN,', ','), ',') ELSE tags END) "
                     "WHERE uid=? AND status='active'",
                     (new_uid, nvf, ts, ts, old_uid))
        if conn.execute("SELECT changes()").fetchone()[0] == 0:
            conn.rollback()
            return {"ok": False,
                    "error": ("fact %s is not active (concurrent modification "
                              "or already superseded)" % old_uid)}
        # 新事实写入
        conn.execute(
            """INSERT INTO facts (uid,type,subject,content,status,source,scope,confidence,
                                  created_at,updated_at,valid_from,recorded_at,temporal_source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_uid, old['type'], old['subject'], new_content, 'active',
             by_agent, old['scope'], 1.0, ts, ts, nvf, ts, 'native'))
        conn.execute("INSERT INTO supersessions (old_uid,new_uid,reason,by_agent,ts) VALUES (?,?,?,?,?)",
                     (old_uid, new_uid, reason, by_agent, now()))
        _pre_audit = conn.execute("SELECT COUNT(*) FROM audit_log WHERE op='correct' AND target=?", (old_uid,)).fetchone()[0]
        audit(conn, 'correct', old_uid, by_agent, '%s -> %s' % (old['content'][:40], new_content[:40]))
        _post_audit = conn.execute("SELECT COUNT(*) FROM audit_log WHERE op='correct' AND target=?", (old_uid,)).fetchone()[0]
        conn.commit()
        # 2026-11-06 (Persome edit.py P0#5): mutation landed but the audit
        # receipt did not = the supersede is NOT done. Fail loudly rather
        # than returning ok=true with an unprovable change.
        if _post_audit <= _pre_audit:
            raise RuntimeError(
                '[P0#5] correct supersede landed but audit receipt did not '
                'persist - the change is not provable; refusing to report success')
        # 向量索引同步：旧的下线、新的上线
        _vec_delete(old_uid)
        _vec_upsert(new_uid, new_content, old['type'], by_agent)
        # A1: auto-bump Q-Value — corrected fact is wrong, give negative reward
        try:
            bump_qvalue(old_uid, reward=0.0, agent=by_agent, detail='auto: corrected/superseded')
        except Exception:
            pass
        # 2026-11-07 (Persome P1#8): retrieval-stats carry-over. use_count is
        # earned attention; superseding must not leave the successor with less
        # than the predecessor ended with. Runs AFTER the auto-bump so it sees
        # the final old count (Persome entries.py L663-671 carry-over).
        try:
            _final_old = conn.execute(
                "SELECT use_count FROM facts WHERE uid=?", (old_uid,)).fetchone()
            _carry = int(_final_old[0] or 0) if _final_old else 0
            if _carry > 0:
                conn.execute(
                    "UPDATE facts SET use_count = ? WHERE uid = ? AND (use_count IS NULL OR use_count < ?)",
                    (_carry, new_uid, _carry))
                conn.commit()
        except Exception:
            pass  # older schema without use_count — never block the correction
        # 2026-11-07 (Hindsight retractions): propagate to sibling facts
        _retracted = _propagate_retraction(conn, old_uid, 'superseded')
        conn.commit()
        return {'ok': True, 'old_uid': old_uid, 'new_uid': new_uid, 'op': 'supersede', 'retracted_siblings': len(_retracted)}
    finally:
        conn.close()




# ============================================================================
# 2026-11-07 (Hindsight observations.py): entity trend computation
# Pure math from fact timestamps. Zero LLM. For each entity, compare evidence
# density in recent 30d vs older 90d to compute STABLE/STRENGTHENING/WEAKENING/
# NEW/STALE. Surfaces in rebuild and search so agents know if a fact is
# gaining or losing supporting evidence over time.
# ============================================================================

def compute_entity_trends(conn, recent_days=30, old_days=90):
    """Compute temporal trend per entity from fact_entities timestamps.

    Returns {entity: {'trend': str, 'proof_count': int, 'recent_n': int, 'old_n': int}}.
    Zero LLM — deterministic from created_at timestamps alone.
    """
    import datetime as _dt
    _now = _dt.datetime.now()
    _recent_cut = (_now - _dt.timedelta(days=recent_days)).strftime('%Y-%m-%d')
    _old_cut = (_now - _dt.timedelta(days=old_days)).strftime('%Y-%m-%d')

    # Get per-entity evidence counts split by recency
    try:
        rows = conn.execute("""
            SELECT fe.entity, COUNT(*) as total,
                   SUM(CASE WHEN f.created_at > ? THEN 1 ELSE 0 END) as recent_n,
                   SUM(CASE WHEN f.created_at < ? THEN 1 ELSE 0 END) as old_n,
                   SUM(CASE WHEN f.created_at BETWEEN ? AND ? THEN 1 ELSE 0 END) as mid_n
            FROM fact_entities fe
            JOIN facts f ON f.uid = fe.fact_uid AND f.status = 'active'
            GROUP BY fe.entity
            HAVING COUNT(*) >= 2
        """, (_recent_cut, _old_cut, _old_cut, _recent_cut)).fetchall()
    except Exception:
        return {}

    trends = {}
    for r in rows:
        entity, total, recent_n, old_n, mid_n = r[0], r[1], r[2] or 0, r[3] or 0, r[4] or 0

        # Hindsight's algorithm (observations.py compute_trend):
        if recent_n == 0:
            trend = 'STALE'
        elif old_n == 0 and mid_n == 0:
            trend = 'NEW'
        else:
            # Compare density (evidence per day)
            recent_density = recent_n / recent_days if recent_days > 0 else 0
            older_period = old_days - recent_days
            older_density = (old_n + mid_n) / older_period if older_period > 0 else 0

            if older_density == 0:
                trend = 'NEW'
            else:
                ratio = recent_density / older_density
                if ratio > 1.5:
                    trend = 'STRENGTHENING'
                elif ratio < 0.5:
                    trend = 'WEAKENING'
                else:
                    trend = 'STABLE'

        trends[entity] = {
            'trend': trend,
            'proof_count': total,
            'recent_n': recent_n,
            'old_n': old_n + mid_n,
        }
    return trends


# ============================================================================
# 2026-11-07 (Hindsight evidence grounding): per-entity evidence chain
# For any entity, return all active facts that reference it, with uid, content
# snippet, timestamp and source. This is the "evidence" that would back an
# observation — exactly what Hindsight stores in ObservationEvidence.
# Zero LLM: pure SQL aggregation over fact_entities JOIN facts.
# ============================================================================

def get_entity_evidence(conn, entity, attr=None, limit=20):
    """Return evidence chain for an entity: all active facts citing it.

    Hindsight's ObservationEvidence has: memory_id, quote, relevance, timestamp.
    Ours maps to: uid, content (truncated), source, created_at.
    When attr is given, only facts tagged with that specific attribute are returned.

    Returns list of dicts sorted by created_at DESC (most recent evidence first).
    """
    sql = ("SELECT fe.fact_uid, f.content, f.source, f.created_at, f.type, "
           "fe.entity, fe.attr "
           "FROM fact_entities fe "
           "JOIN facts f ON f.uid = fe.fact_uid AND f.status = 'active' "
           "WHERE fe.entity = ?")
    params = [entity]
    if attr:
        sql += " AND fe.attr LIKE ?"
        params.append(f'%{attr}%')
    sql += " ORDER BY f.created_at DESC LIMIT ?"
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []

    evidence = []
    for r in rows:
        evidence.append({
            'uid': r[0],
            'quote': (r[1] or '')[:200],  # truncate to evidence-sized quote
            'source': r[2],
            'timestamp': r[3],
            'type': r[4],
            'entity': r[5],
            'attr': r[6],
        })
    return evidence


def extract_entities_from_content(conn, content):
    """Extract entity names from content that exist in fact_entities.

    Used by search to find which entities a hit's content references.
    Zero LLM: matches against already-stored entity names.
    """
    try:
        _all_entities = [r[0] for r in conn.execute(
            "SELECT DISTINCT entity FROM fact_entities LIMIT 5000").fetchall()]
    except Exception:
        return []

    if not _all_entities or not content:
        return []

    _content_lower = content.lower()
    _matched = []
    for _e in _all_entities:
        if len(_e) >= 2 and _e.lower() in _content_lower:
            _matched.append(_e)
    return _matched[:10]


# ============================================================================
# 2026-11-07 (EverOS dead-letter awareness): write-only data audit
# Scan all tables and key columns for data that gets written but never read.
# Surfaces in governance stats so operators can spot waste (like EverOS's
# foresight strategy: "running it by default spends tokens on write-only data").
# ============================================================================

# Tables + key columns to audit
_DEAD_LETTER_COLUMNS = [
    ('facts', 'predicate'), ('facts', 'q_value'), ('facts', 'use_count'),
    ('facts', 'verify_at'), ('facts', 'expires_at'), ('facts', 'subject'),
    ('facts', 'confidence'), ('facts', 'temporal_source'),
    ('tool_assets', 'aliases'), ('tool_assets', 'capabilities'),
    ('recipes', 'task_patterns'), ('recipes', 'steps'), ('recipes', 'pitfalls'),
    ('supersessions', 'reason'),
    ('conflict_reviews', 'verdict'), ('conflict_reviews', 'note'),
    ('candidates', 'content'), ('candidates', 'type'), ('candidates', 'reason'),
]

def dead_letter_audit(conn):
    """Audit write-only data: tables/columns that get written but have no read path.

    Returns list of {table, column, row_count, has_read} for suspicious entries.
    A column is "dead" if: it has data (row_count > 0) AND our source code
    never SELECTs it.
    """
    import ast as _ast
    _gw_src = open(__file__, encoding='utf-8').read()
    # Also scan governance + memsearch + mcp_server for reads
    for _extra in ('governance.py', 'memsearch.py', 'mcp_server.py', 'memtether.py'):
        try:
            _gw_src += open(_extra, encoding='utf-8').read()
        except Exception:
            pass
    _src_lower = _gw_src.lower()

    dead = []
    # Table-level check
    _tables = ['facts', 'tool_assets', 'recipes', 'supersessions', 'audit_log',
               'fact_entities', 'conflict_reviews', 'candidates', 'run_events']
    for _t in _tables:
        # Has writes? (INSERT/UPDATE in source)
        _has_write = any(f'{_t}' in _src_lower and kw in _src_lower
                        for kw in ('insert into', 'update '))
        # Has reads? (SELECT ... FROM _t)
        _has_read = f'select' in _src_lower and f'from {_t}' in _src_lower
        # Has data?
        try:
            _count = conn.execute(f'SELECT COUNT(*) FROM {_t}').fetchone()[0]
        except Exception:
            continue
        if _has_write and not _has_read and _count > 0:
            dead.append({'table': _t, 'column': '*', 'row_count': _count,
                        'issue': 'written but never read'})

    # Column-level check
    for _t, _c in _DEAD_LETTER_COLUMNS:
        try:
            _count = conn.execute(
                f'SELECT COUNT(*) FROM {_t} WHERE {_c} IS NOT NULL AND {_c} != \'\''
            ).fetchone()[0]
        except Exception:
            continue
        if _count == 0:
            continue
        # Check if any SELECT mentions this column
        _col_read = _c in _src_lower
        if not _col_read:
            dead.append({'table': _t, 'column': _c, 'row_count': _count,
                        'issue': 'column has data but source never references it in any SELECT'})

    return dead

# ============================================================================
# 2026-11-07 (Hindsight retractions.py): retraction tracking
# When a fact is superseded or retired, find all OTHER active facts that share
# an entity with it (via fact_entities) and tag them with "retracted:<uid>".
# This lets search and rebuild surface "the fact this referenced is now dead"
# instead of silently serving stale cross-references.
# ============================================================================

def _propagate_retraction(conn, old_uid, action='superseded'):
    """Find active facts sharing an entity with old_uid and tag them.

    Hindsight's approach (retractions.py): when a fact dies, the documents
    citing it should know "your grounding is gone". We do the equivalent:
    facts that share an entity with a dead fact get a `retracted:<uid>` tag
    so search/rebuild can annotate them.

    This is deterministic (SQL only, zero LLM) and bounded (max 50 tags per
    propagation to avoid runaway fan-out on common entities).
    """
    # 1. Get entities of the dead fact
    try:
        _dead_entities = [r[0] for r in conn.execute(
            "SELECT DISTINCT entity FROM fact_entities WHERE fact_uid=?",
            (old_uid,)).fetchall()]
    except Exception:
        return []  # fact_entities may not exist on fresh DBs

    if not _dead_entities:
        return []

    # 2. Find active facts sharing any of those entities (not old_uid itself)
    _placeholders = ','.join('?' * min(len(_dead_entities), 20))
    _params = list(_dead_entities[:20]) + [old_uid]
    try:
        _affected = conn.execute(
            "SELECT DISTINCT fe.fact_uid FROM fact_entities fe "
            "JOIN facts f ON f.uid = fe.fact_uid "
            "WHERE fe.entity IN ({}) "
            "AND f.uid != ? AND f.status = 'active'".format(_placeholders),
            _params).fetchall()
    except Exception:
        return []

    _tagged = []
    for _r in _affected[:50]:  # bounded fan-out
        _target_uid = _r[0]
        _tag = 'retracted:' + old_uid
        try:
            _cur_tags = conn.execute(
                "SELECT tags FROM facts WHERE uid=?", (_target_uid,)).fetchone()
            if _cur_tags and _cur_tags[0]:
                _tags_list = [t.strip() for t in _cur_tags[0].split(',') if t.strip()]
                if _tag not in _tags_list:
                    _tags_list.append(_tag)
                    conn.execute("UPDATE facts SET tags=? WHERE uid=?",
                                 (','.join(_tags_list), _target_uid))
                    _tagged.append(_target_uid)
            else:
                conn.execute("UPDATE facts SET tags=? WHERE uid=?",
                             (_tag, _target_uid))
                _tagged.append(_target_uid)
        except Exception:
            pass

    # 3. Audit log the propagation
    if _tagged:
        try:
            audit(conn, 'retraction_propagate', old_uid,
                  'system',
                  '{}: tagged {} facts with retracted:{}'.format(action, len(_tagged), old_uid))
        except Exception:
            pass

    return _tagged


def retire(uid, reason, by_agent=DEFAULT_SOURCE):
    """退役机制/工具。双时间轴：valid_to 与 invalidated_at 都记当前时刻
    （退役是"现在就判定不再有效"的动作，两轴在此重合；若某条事实是
     事后才补记退役，则应改用 governance.retire 并另行指定 valid_to）。"""
    # ★2026-09-22 修：retire 是全库唯一不校验来源的写入函数。
    by_agent = _guard_source(by_agent)
    init_db()
    conn = get_conn()
    try:
        ts = now()
        # ★2026-09-24：退役时清掉 pin tag（同 memory_hub）
        conn.execute("UPDATE facts SET status='retired', valid_to=?, invalidated_at=?, "
                     "updated_at=?, tags=(CASE WHEN (',' || lower(REPLACE(COALESCE(tags,''), ' ', ',')) || ',') LIKE '%,pin,%' "
                     "THEN trim(replace(replace(',' || tags || ',', ',pin,', ','), ',PIN,', ','), ',') ELSE tags END) "
                     "WHERE uid=?",
                     (ts, ts, ts, uid))
        conn.execute("UPDATE tool_assets SET status='retired', updated_at=? WHERE uid=?",
                     (ts, uid))
        audit(conn, 'retire', uid, by_agent, reason)
        conn.commit()
        _vec_delete(uid)
        # A1: auto-bump Q-Value — retired fact is expired, give negative reward
        try:
            bump_qvalue(uid, reward=0.0, agent=by_agent, detail='auto: retired')
        except Exception:
            pass
        # 2026-11-07 (Hindsight retractions): propagate to sibling facts
        _retracted = _propagate_retraction(conn, uid, 'retired')
        conn.commit()
        return {'ok': True, 'uid': uid, 'op': 'retired', 'retracted_siblings': len(_retracted)}
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


def _rev_str(s):
    """把字符串变成一个"越大越排前"的可比较键（用于元组排序里模拟 reverse）。

    做法：按字符码位取补，保证 'z' < 'a'（即字典序反转），且同长度可比。
    """
    return ''.join(chr(0x10FFFF - ord(c)) for c in (s or ''))


def profile(subject='user', scope=None, max_facts=200, include_retired=False):
    """把某个 subject 的 active 记忆聚合成一份**确定性表征**（零 LLM、零幻觉）。

    ★2026-10-19 全网功能对标产物。对标对象：
      - supermemory (31.3K★) `get_profile` —— "~50ms user profiles"
      - honcho (7.7K★) `get_representation` —— "contextualized snapshot
        insertable into a system prompt"
    两者都是「对某个 user/peer 的持续表征」。MemTether 此前只有 subject
    字段（能按 subject 过滤）但没有「聚合出表征」的入口。

    ★为什么用确定性聚合而不是 LLM 蒸馏：
      项目公理是"禁花架子 + 禁 AI 幻觉"。LLM 总结会引入编造风险，
      且让 profile 的内容不可复现（同一次查询两次不同结果）。
      确定性聚合 = 同一份库永远得到同一份 profile，可逐条回溯到 uid。

    返回：
      {
        'subject': str,
        'counts': {type: n},
        'newest': 'YYYY-MM-DD', 'oldest': 'YYYY-MM-DD',
        'top_facts': [ {uid, type, confidence, q_value, created_at, lead} ],  # 按 q_value+recency
        'high_confidence': [...],   # confidence >= 0.9
        'recently_corrected': [...],# 近 30 天有新版本的
        'sources': {source: n},
        'l0_digest': str,           # 所有 L0 拼接（可直接插入 system prompt）
      }
    """
    init_db()
    conn = get_conn()
    try:
        sql = ("SELECT uid, type, content, confidence, q_value, source, created_at, "
               "updated_at FROM facts WHERE subject=?")
        args = [subject]
        if not include_retired:
            sql += " AND status='active'"
        if scope:
            sql += " AND scope=?"
            args.append(scope)
        sql += " ORDER BY COALESCE(NULLIF(updated_at,''), created_at) DESC LIMIT ?"
        args.append(max_facts)
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        if not rows:
            # 空结果也要保持与非空返回**同一套键**，否则调用方要写
            #   p.get('total', 0) 这种防御代码（第一版就漏了 total，实测踩到）
            return {'subject': subject, 'counts': {}, 'sources': {},
                    'newest': None, 'oldest': None, 'total': 0,
                    'top_facts': [], 'high_confidence_count': 0,
                    'l0_digest': '', 'empty': True}

        counts, sources = {}, {}
        for r in rows:
            counts[r['type']] = counts.get(r['type'], 0) + 1
            sources[r['source']] = sources.get(r['source'], 0) + 1

        dates = sorted((r['created_at'] or '')[:10] for r in rows if r['created_at'])
        # top_facts：q_value 优先，同分看 recency（与投影/检索同口径）
        def _rank(r):
            # 字符串不能取负 → 用 reverse 排序 + 元组（q_value 降序、时间降序）
            return (-(r['q_value'] if r['q_value'] is not None else 0.5),
                    _rev_str(r['updated_at'] or r['created_at'] or ''))
        top = sorted(rows, key=_rank)[:10]
        hi = [r for r in rows if (r['confidence'] or 0) >= 0.9][:10]

        # L0 摘要：用 memsearch 的确定性分层，保证与检索侧同一把尺子
        l0s = []
        try:
            sys.path.insert(0, HUB)
            import memsearch as _ms
            for r in top:
                l0s.append('- ' + _ms.content_layers(r['content'])['L0'])
        except Exception:
            for r in top:
                l0s.append('- ' + (r['content'] or '')[:80])

        return {
            'subject': subject,
            'counts': counts,
            'sources': sources,
            'newest': dates[-1] if dates else None,
            'oldest': dates[0] if dates else None,
            'total': len(rows),
            'top_facts': [{'uid': r['uid'], 'type': r['type'],
                           'confidence': r['confidence'], 'q_value': r['q_value'],
                           'created_at': (r['created_at'] or '')[:10],
                           'lead': (r['content'] or '')[:80]} for r in top],
            'high_confidence_count': len(hi),
            'l0_digest': '\n'.join(l0s),
        }
    finally:
        conn.close()


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
                capabilities='', known_failures='[]', prerequisites='', source=DEFAULT_SOURCE):
    source = _guard_source(source)
    # F7 fix (2026-10-13): sensitive gate on tool fields too.
    if _SENSITIVE_BLOCK:
        for _fld in (known_failures, entrypoint or '', prerequisites or ''):
            _sb_rt, _bl_rt, _rd_rt, _hh_rt = _sensitive_check_with_policy(_fld)
            if _sb_rt:
                raise ValueError(
                    '[P0-03] 敏感内容闸门(record_tool)：字段含完整凭据 %s，拒绝入库。'
                    % ', '.join(_bl_rt))
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


def search(query, limit=10, mem0=False, layer='L2', l1_sentences=3):
    """混合检索（2026-09-13 升级版）：质量门禁 + 向量 + ASCII精确 + 融合排序。
    经 Astra 多轮协作确认；8 条回归查询 8/8 通过（旧 LIKE 版仅 4/8）。
    失败时自动退化到旧 LIKE 检索。

    ★2026-10-19 新增 layer 参数（对标 OpenViking L0/L1/L2 分层）：
      layer='L2'（缺省）= 全文，与旧行为逐字节一致
      layer='L0' = 每条只回首句结论（一行），适合"先扫一遍"
      layer='L1' = 每条回前 N 句（默认 3 句），适合"判断要不要展开"
      无论哪层，结果都带 layers={'L0','L1','L2_len'} 元数据，可再按需升级深度。
    """
    try:
        sys.path.insert(0, HUB)
        import memsearch
        r = memsearch.search_hybrid(query, limit=limit, layer=layer,
                                    l1_sentences=l1_sentences)
        if r.get('results'):
            _eng = 'hybrid-degraded' if r.get('degraded') else 'hybrid'
            _stale_n = sum(1 for _x in r['results'] if _x.get('stale_warning'))
            _hint = ''
            if _stale_n > 0:
                _hint = f'{_stale_n}/{len(r["results"])} 条可能已过期（含路径/版本），引用前请复核'
            return {'ok': True, 'query': r['query'], 'results': r['results'], 'semantic': r['results'], 'engine': _eng, 'all_unanswered': r.get('all_unanswered', False), 'degraded': r.get('degraded', False), 'stale_count': _stale_n, 'stale_hint': _hint}
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
    # ★2026-10-19：降级路径（含上面的拆词合并）同样要挂分层元数据，
    #   否则 layer 参数在 fallback 下静默失效。必须放在拆词合并**之后**：
    #   合并会整体替换 r['results']，放在前面会被覆盖掉。
    if layer in ('L0', 'L1') and r.get('results'):
        try:
            sys.path.insert(0, HUB)
            import memsearch as _ms_fb
            r['results'] = _ms_fb.apply_layers(r['results'], layer=layer,
                                               l1_sentences=l1_sentences)
        except Exception:
            pass
    r['engine'] = 'like_fallback (%s)' % _why
    return r


def _search_like_legacy(query, limit=10, mem0=False):
    # P2-1 (2026-10-05): DEPRECATED last-resort fallback. Primary = memsearch.search_hybrid.
    """[旧版，保留作兜底] 关键词 LIKE 精确子串匹配（中文失效，仅 ASCII 有效）。"""
    init_db()
    conn = get_conn()
    try:
        q = query.strip()
        results = []
        # 1) 事实：关键词 LIKE（active 优先，superseded 惩罚）
        for row in conn.execute(
            "SELECT * FROM facts WHERE status='active' AND scope NOT IN ('private','restricted') AND (content LIKE ? OR subject LIKE ? OR tags LIKE ?) ORDER BY updated_at DESC LIMIT ?",
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




def record_recipe(task_patterns, steps, preferred_tool=None, output_paths=None,
                  known_pitfalls=None, fallback_tools=None, source=DEFAULT_SOURCE):
    """P0-2 (2026-10-18): 写入/更新一条任务配方（recipes 表唯一写入口）。

    task_patterns: 逗号分隔触发词（resolve_task 按 LIKE 匹配）
    steps:         list[str]，每步一条命令/动作
    preferred_tool: 可选，首选工具 uid
    output_paths:  list[str] 产物路径
    known_pitfalls: list[str] 已知坑
    fallback_tools: list[str] 备用工具 uid

    uid 稳定 = _uid('recipe', primary_pattern)：同 primary 触发词重复写入 = 更新而非堆叠。
    """
    source = _guard_source(source)
    if isinstance(task_patterns, str):
        patterns = [t.strip() for t in task_patterns.split(',') if t.strip()]
    else:
        patterns = [str(t).strip() for t in (task_patterns or []) if str(t).strip()]
    if not patterns:
        return {'ok': False, 'error': 'task_patterns must contain at least 1 pattern'}
    if not steps or not [s for s in steps if str(s).strip()]:
        return {'ok': False, 'error': 'steps must be non-empty'}
    primary = patterns[0]
    # P0-2 fix: _uid embeds a timestamp -> same pattern would create a NEW row
    # every write (defeats upsert). Stable uid = md5(primary) only.
    uid = 'recipe-' + hashlib.md5(primary.encode('utf-8')).hexdigest()[:16]
    steps_json = json.dumps([str(s) for s in steps], ensure_ascii=False)
    op_json = json.dumps([str(p) for p in (output_paths or [])], ensure_ascii=False)
    pit_json = json.dumps([str(p) for p in (known_pitfalls or [])], ensure_ascii=False)
    fb_json = json.dumps([str(f) for f in (fallback_tools or [])], ensure_ascii=False)
    init_db()
    conn = get_conn()
    try:
        existing = conn.execute("SELECT uid FROM recipes WHERE uid=?", (uid,)).fetchone()
        if existing:
            conn.execute(
                """UPDATE recipes SET task_patterns=?, preferred_tool=?, steps=?,
                   output_paths=?, known_pitfalls=?, fallback_tools=?,
                   last_success_at=?, updated_at=? WHERE uid=?""",
                (','.join(patterns), preferred_tool, steps_json, op_json, pit_json,
                 fb_json, now(), now(), uid))
            audit(conn, 'record_recipe_update', uid, source, primary)
            conn.commit()
            return {'ok': True, 'uid': uid, 'op': 'update'}
        conn.execute(
            """INSERT INTO recipes (uid,task_patterns,preferred_tool,steps,output_paths,
               known_pitfalls,fallback_tools,last_success_at,source,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, ','.join(patterns), preferred_tool, steps_json, op_json,
             pit_json, fb_json, now(), source, now(), now()))
        audit(conn, 'record_recipe', uid, source, primary)
        conn.commit()
        return {'ok': True, 'uid': uid, 'op': 'insert'}
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
        # P1-fix (a42): keys are env-first (portable). Optionally point
        # MEM_CRED_ENV_PATH at a local module exposing env() to populate them.
        cred_path = os.environ.get('MEM_CRED_ENV_PATH', '')
        if cred_path and os.path.isfile(cred_path) and cred_path not in sys.path:
            sys.path.insert(0, os.path.dirname(cred_path))
            try:
                mod = __import__(os.path.splitext(os.path.basename(cred_path))[0])
                if hasattr(mod, 'env'):
                    mod.env()
            except Exception:
                pass
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
        try:
            r = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as _he:
            _code = getattr(_he, 'code', 0)
            # ★2026-10-19：区分「key 失效」与「临时故障」。401/403 是**永久**失败
            #   （deepseek key 09-15 已失效，中枢有 active 记录），重试无意义
            #   → 用 _fatal 标记让上层 process_events 走 skip 而不是 failed。
            if _code in (401, 403):
                return {'_error': 'auth_failed_%d' % _code, '_fatal': True}
            return {'_error': 'http_%d' % _code, '_fatal': False}
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
        return {'_error': str(e)[:200], '_fatal': False}


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
        # ★2026-10-19：把 _fatal 透传出去，否则上层无法区分「key 失效」与「临时故障」
        #   （实测漏传导致 process_events 仍标 failed，重试计数白涨）。
        return {'ok': False, 'error': (res or {}).get('_error', 'llm_failed'),
                '_fatal': bool((res or {}).get('_fatal'))}
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
        # Mirror of memory_hub/gateway.py (2026-10-22): explicitly exclude
        # telemetry rows. The old query filtered on status='pending' alone, so
        # ANY row written to run_events would be fed to auto_reflect as an agent
        # event the moment it became pending. The codex hook writes
        # event_type='memory_inject' rows there; those have no payload to
        # distil. Whitelisting the type beats relying on "telemetry rows are
        # never pending", which is luck, not design.
        # NOTE: dual_repo_guard only compares def/class/SCHEMA NAMES, not
        # function bodies - so this mirror had to be done by hand.
        _TELEMETRY_TYPES = ('memory_inject',)
        _ph = ','.join('?' * len(_TELEMETRY_TYPES))
        rows = conn.execute(
            "SELECT * FROM run_events WHERE status='pending' "
            "AND event_type NOT IN (%s) "
            "ORDER BY created_at ASC LIMIT ?" % _ph,
            _TELEMETRY_TYPES + (limit,)).fetchall()
        out = {'processed': 0, 'skipped': 0, 'failed': 0, 'details': []}
        for row in rows:
            try:
                payload = json.loads(row['payload_json'])
                r = auto_reflect(row['run_id'], payload, model='deepseek')
                if r.get('ok'):
                    conn.execute("UPDATE run_events SET status='processed', processed_at=? WHERE id=?",
                                 (now(), row['id']))
                    out['processed'] += 1
                elif r.get('_fatal'):
                    # ★2026-10-19：key 失效（401/403）是**永久**失败，不是"待重试"。
                    #   旧行为标 failed + retry_count++，每跑一次 process_events
                    #   就多一条 failed 记录且永远不会好（实测 3 条 failed 全是这个原因）。
                    conn.execute("UPDATE run_events SET status='skipped', error=?, processed_at=? WHERE id=?",
                                 ((r.get('error') or '')[:200], now(), row['id']))
                    out['skipped'] += 1
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


def incident(step, error, workaround='', result='', run_id=None, agent=DEFAULT_SOURCE):
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
        # P5 (2026-10-05): L0-L3 layered projection counts
        out['levels'] = {
            'L0_raw_conversations': conn.execute(
                "SELECT COUNT(*) FROM facts WHERE type='experience' AND status='active'").fetchone()[0],
            'L1_atomic_facts': conn.execute(
                "SELECT COUNT(*) FROM facts WHERE type IN ('fact','incident') AND status='active'").fetchone()[0],
            'L2_decisions': conn.execute(
                "SELECT COUNT(*) FROM facts WHERE type IN ('decision','procedure') AND status='active'").fetchone()[0],
            'L3_preferences': conn.execute(
                "SELECT COUNT(*) FROM facts WHERE type IN ('preference','environment') AND status='active'").fetchone()[0],
            'tool_assets': conn.execute(
                "SELECT COUNT(*) FROM tool_assets WHERE status='active'").fetchone()[0],
        }
        # 2026-11-07 (EverOS dead-letter awareness): surface write-only data
        try:
            out['dead_letters'] = dead_letter_audit(conn)
        except Exception:
            out['dead_letters'] = []
        return out
    finally:
        conn.close()


def set_pin(uid=None, on=True):
    """★2026-09-16 七修：把某条记忆**钉住**（tags 加 pin），使其无条件进入投影。

    背景：投影本质是「最近 N 条」而非「最重要 N 条」—— 新写的 experience/incident
    会把早先写下的定义类 fact 挤出 4000 字符槽位，表现为**同一个概念反复记不住**
    （实测痛点：同一个定义类概念，连问三次仍答不上来）。钉住 = 把该条排除在
    「时间竞争」之外，因为"定义"恰恰最不该随时间沉底。

    设计：不新增 schema，复用 tags 字段（逗号分隔）。
          uid 省略时**列出**当前所有已钉住条目（只读）。

    用法：
        python gateway.py pin                       # 列出已钉住
        python gateway.py pin <uid>                 # 钉住
        python gateway.py pin <uid> --off           # 取消钉住
    改完必须 rebuild 才生效。
    """
    init_db()
    conn = get_conn()
    try:
        if not uid:
            rows = conn.execute(
                "SELECT uid, type, substr(content,1,70) AS lead FROM facts "
                "WHERE status='active' AND (',' || lower(REPLACE(COALESCE(tags,''), ' ', ',')) || ',') LIKE '%,pin,%' "
                "ORDER BY COALESCE(NULLIF(updated_at,''), created_at) DESC").fetchall()
            return {'ok': True, 'pinned_count': len(rows), 'items': [dict(r) for r in rows]}
        r = conn.execute("SELECT uid, type, tags, content FROM facts WHERE uid=?",
                         (uid,)).fetchone()
        if not r:
            return {'ok': False, 'error': 'uid 不存在', 'uid': uid}
        tags = [t.strip() for t in (r['tags'] or '').split(',') if t.strip()]
        has = 'pin' in [t.lower() for t in tags]
        if on and not has:
            tags.append('pin')
        elif not on and has:
            tags = [t for t in tags if t.lower() != 'pin']
        conn.execute("UPDATE facts SET tags=? WHERE uid=?", (','.join(tags), uid))
        conn.commit()
        return {'ok': True, 'uid': uid, 'pinned': bool(on), 'tags': ','.join(tags),
                'lead': (r['content'] or '')[:70],
                'next': 'python gateway.py rebuild  # 投影不会自动更新'}
    finally:
        conn.close()


# ★升级 1（Q-Value）回写参数 —— 与 memsearch.py 的检索因子 (QVALUE_BASE + QVALUE_SPAN*q) 配套。
QVALUE_LR = 0.1      # 学习率：q += LR * (reward - q)。0.1 = 约 10 次观测收敛到长期均值
QVALUE_INIT = 0.5    # 中性初值，也是「从未被采纳」的默认值


def bump_qvalue(uid=None, reward=1.0, agent=DEFAULT_SOURCE, detail='', apply=True):
    """★升级 1（Q-Value）：检索命中**被采纳后**回写价值分，让好记忆自己浮上来。

    背景：本中枢的生命周期卡在 manage/update 段 —— 写入做得好、检索做一半，
    但「哪条记忆真的有用」这个信号**从未被记录**。结果：投影与检索都只能靠
    时间/相似度排序，历史高频复用的结论会被新写入挤下去。

    公式：q_value += LR * (reward - q_value)
        reward ∈ [0,1]：1.0=完全采纳（直接解决问题）/ 0.5=部分有用 / 0.0=检索到但没用
        收敛性：q 单调收敛到该条被采纳的长期平均 reward，故不会无限膨胀。
    检索侧因子：score *= (0.3 + 0.7 * q_value)
        0.5 → 0.65（中性，也是全库默认值，故**不改变任何现有排序**）
        1.0 → 1.00（上浮上限）
        0.0 → 0.30（下沉下限，**不为 0**：避免误判把条目钉死，仍可被强相关救回）

    ★这是**显式**接口，不自动乱写库。只有检索结果被真正采纳时，调用方才应回写。
    uid 省略时**列出**当前分布（只读）。apply=False 时只算不写（dry-run）。
    每次写入都落 audit_log（op='qvalue'），可追溯是谁、因为什么把分调上去的。

    用法：
        python gateway.py qvalue                     # 看分布（只读）
        python gateway.py qvalue <uid>               # 采纳一次（reward=1.0）
        python gateway.py qvalue <uid> --reward 0.5  # 部分有用
        python gateway.py qvalue <uid> --dry-run     # 只算不写
    """
    try:
        reward = float(reward)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'reward 必须是 0~1 之间的数字', 'reward': reward}
    if not (0.0 <= reward <= 1.0):
        return {'ok': False, 'error': 'reward 必须落在 [0,1]', 'reward': reward}

    init_db()
    conn = get_conn()
    try:
        if not uid:
            # ★2026-09-20：facts + tool_assets 合并统计（资产也有 q_value 了）
            r = conn.execute(
                "SELECT COUNT(*) n,"
                " SUM(CASE WHEN q_value IS NULL THEN 1 ELSE 0 END) nulls,"
                " AVG(q_value) avg_q, MIN(q_value) min_q, MAX(q_value) max_q,"
                " SUM(COALESCE(use_count,0)) uses"
                " FROM facts WHERE status='active'").fetchone()
            ra = conn.execute(
                "SELECT COUNT(*) n,"
                " SUM(CASE WHEN q_value IS NULL THEN 1 ELSE 0 END) nulls,"
                " SUM(COALESCE(use_count,0)) uses"
                " FROM tool_assets WHERE status='active'").fetchone()
            # ★UNION 的 ORDER BY 只能引用结果集列名（不能是表达式），
            #   故包一层子查询；COALESCE 兜底用字面量（= QVALUE_INIT 0.5）。
            top = conn.execute(
                "SELECT * FROM ("
                " SELECT uid, type, q_value, use_count, substr(content,1,70) AS lead"
                " FROM facts WHERE status='active'"
                " UNION ALL"
                " SELECT uid, 'tool' AS type, q_value, use_count, substr(name,1,70) AS lead"
                " FROM tool_assets WHERE status='active')"
                " ORDER BY COALESCE(q_value, 0.5) DESC,"
                " COALESCE(use_count, 0) DESC LIMIT 10").fetchall()
            return {'ok': True, 'readonly': True, 'active': r['n'],
                    'assets_active': ra['n'],
                    'qvalue_null': (r['nulls'] or 0) + (ra['nulls'] or 0),
                    'avg': round(r['avg_q'] or 0.0, 4),
                    'min': r['min_q'], 'max': r['max_q'],
                    'total_use': (r['uses'] or 0) + (ra['uses'] or 0),
                    'lr': QVALUE_LR, 'init': QVALUE_INIT,
                    'top': [dict(x) for x in top]}

        # ★2026-09-20：uid 不在 facts 时落到 tool_assets（资产也能被 bump）
        r = conn.execute(
            "SELECT uid, type, q_value, use_count, content FROM facts WHERE uid=?",
            (uid,)).fetchone()
        table = 'facts'
        if not r:
            r = conn.execute(
                "SELECT uid, 'tool' AS type, q_value, use_count, name AS content"
                " FROM tool_assets WHERE uid=?",
                (uid,)).fetchone()
            table = 'tool_assets'
        if not r:
            return {'ok': False, 'error': 'uid 不存在（facts 与 tool_assets 均无）',
                    'uid': uid}

        q0 = QVALUE_INIT if r['q_value'] is None else float(r['q_value'])
        q1 = max(0.0, min(1.0, q0 + QVALUE_LR * (reward - q0)))
        n0 = int(r['use_count'] or 0)

        out = {'ok': True, 'uid': uid, 'type': r['type'], 'table': table,
               'q_value_before': round(q0, 6), 'q_value_after': round(q1, 6),
               'reward': reward, 'lr': QVALUE_LR,
               'use_count_before': n0, 'use_count_after': n0 + 1,
               'score_factor': round(0.3 + 0.7 * q1, 6),
               'applied': bool(apply),
               'lead': (r['content'] or '')[:70]}
        if apply:
            conn.execute("UPDATE %s SET q_value=?, use_count=? WHERE uid=?" % table,
                         (round(q1, 6), n0 + 1, uid))
            audit(conn, 'qvalue', uid, agent,
                  detail or ('reward=%g  q %.4f->%.4f  use %d->%d'
                             % (reward, q0, q1, n0, n0 + 1)))
            conn.commit()
        else:
            out['next'] = '去掉 --dry-run 才会真正写库'
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
        for row in conn.execute("SELECT * FROM facts WHERE status='active' AND scope NOT IN ('private','restricted') ORDER BY updated_at DESC").fetchall():
            typ = row['type'] if row['type'] in sink else 'fact'
            sink[typ].append({
                'text': row['content'], 'source': row['source'], 'tag': row['tags'] or '',
                'ts': row['updated_at'] or row['created_at'], 'status': row['status'],
                'uid': row['uid'], 'confidence': row['confidence'],
            })
        # a75-r6: atomic write - plain open(w) truncates the file on crash
        # and lets concurrent readers see a half-written projection.
        _hg_write(SINK, json.dumps(sink, ensure_ascii=False, indent=2),
                  tag='rebuild-sink')

        # 2) 生成 WorkBuddy MEMORY.md
        #    ★2026-09-17：投影行加「来源」标记（问题②）。图例只在有 hubguard 时加，
        #      避免"标了来源却没解释"和"没标来源却留图例"两种半吊子状态。
        _legend = ('- 行首标记 `类型·来源`；来源 w=国内版 · a=国际版 · o=OpenClaw · d=豆包。'
                   if HG is not None else None)
        mem_lines = [
            '# MEMORY.md — 记忆中枢投影（导航版）',
            '<!-- 真源：%s；由 gateway.py rebuild 生成，勿手改 -->' % os.path.join(HUB, 'memory.db'),
            '',
            '## 怎么用',
            '- 取全文：cd %s && memtether search "<关键词>"' % HUB.replace('\\', '/'),
            '- 写记忆：python gateway.py remember "<内容>" --type fact|decision|incident|experience',
            '- 禁止直接改本文件与 sink.json，一律走 gateway.py。',
            '- 下全称否定结论前先全盘搜索；动手前可用 resolve_task 取工具配方。',
        ]
        if _legend:
            mem_lines.append(_legend)
        mem_lines += [
            '',
            '## 关键事实（active，新→旧，每条仅首句结论）',
        ]
        # 关键事实：**按类型配额**选，再按时间倒序合并。
        # 🔴 2026-09-14 修：旧实现是 `type IN ('fact','decision') LIMIT 80`
        #    → incident / experience **被整体丢弃**（实测 216 条 active 里 70 条永远进不了投影，
        #      其中包括 ACL 拒写的坑、判驱动/判病毒的方法论、会话卡顿真因 —— 恰恰是最该被记住的）。
        #    另外注入侧会按体积截断，所以顺序必须"新→旧"，这样截断只丢最老的。
        # ★P2-9 (2026-10-18): 配额动态化——静态配额在 active 数远超配额时
        # 绝大多数记忆永远进不了投影（实测 fact 45 vs 1022 = 95% 不可见）。
        # 动态规则 = max(静态基数, 该类型 active 数 × 5%)，保证配额随库增长。
        # MEM_PROJ_STATIC_QUOTA=1 可回退旧行为（评测对照用）。
        # 2026-11-04 sync: hub build already includes procedure (base 5) and a
        # today-rescue row set. The release build lacked both, so procedure
        # could starve (observed live: "类型饿死：incident / procedure") and
        # same-day facts were not protected under budget pressure. Mirror hub.
        if os.environ.get('MEM_PROJ_STATIC_QUOTA') == '1':
            _QUOTA = (('decision', 40), ('incident', 20), ('fact', 45),
                      ('experience', 25), ('procedure', 5))
        else:
            _QUOTA = []
            for _typ, _base in (('decision', 40), ('incident', 20), ('fact', 45),
                                ('experience', 25), ('procedure', 5)):
                _n = conn.execute(
                    "SELECT COUNT(*) FROM facts WHERE status='active' AND type=?",
                    (_typ,)).fetchone()[0]
                _QUOTA.append((_typ, max(_base, int(_n * 0.03))))
            _QUOTA = tuple(_QUOTA)
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

        # ★2026-09-16 七修（pin 钉住）：tags 含 pin 的条目**无条件**进入投影。
        #   根因：投影本质是「最近 N 条」而非「最重要 N 条」—— 新写的 experience/incident
        #   会把早先写下的定义类 fact 挤出槽位，表现为**同一个概念反复记不住**
        #   （实测痛点：同一个定义类概念，连问三次仍答不上来）。而"定义"恰恰最不该随时间沉底。
        #   机制：不新增 schema，复用既有 tags 字段（逗号分隔）。
        #     写入侧：python gateway.py remember "…" --tags pin
        #     选取侧：先按配额捞，再把 pin 条目**补进**（防其因配额上限/时间序被排除）。
        #   注意：这里只负责"选中"，不负责"排在前面"—— 排序交给下方 _fill_order。
        _pin_rows = conn.execute(
            "SELECT * FROM facts WHERE status='active' AND (',' || lower(REPLACE(COALESCE(tags,''), ' ', ',')) || ',') LIKE '%,pin,%'"
        ).fetchall()
        _have_uid = {p[2]['uid'] for p in _picked}
        for _r in _pin_rows:
            if _r['uid'] not in _have_uid:
                _picked.append((_r['updated_at'] or _r['created_at'] or '',
                                _r['type'] if _r['type'] in sink else 'fact', _r))
        _picked.sort(key=lambda p: p[0], reverse=True)

        # ★R-today (2026-11-04 sync from hub): same-day facts get a protected
        # slot right after pins. Without this, a fact written today can be
        # squeezed out under budget pressure in the release build while the
        # hub build keeps it — divergent behaviour between maintainer and
        # users. Mirrors hub gateway today_idx semantics.
        import datetime as _dt_today
        _today_str = _dt_today.datetime.now().strftime('%Y-%m-%d')
        _today_rows = conn.execute(
            "SELECT * FROM facts WHERE status='active' AND substr(COALESCE(created_at,''),1,10) = ? "
            "ORDER BY created_at DESC LIMIT 5",
            (_today_str,)).fetchall()
        _have_uid = {p[2]['uid'] for p in _picked}
        for _r in _today_rows:
            if _r['uid'] not in _have_uid:
                _picked.append((_r['updated_at'] or _r['created_at'] or '',
                                _r['type'] if _r['type'] in sink else 'fact', _r))
                _have_uid.add(_r['uid'])
        _picked.sort(key=lambda p: p[0], reverse=True)

        #    🔴 2026-09-14 再修：注入侧按体积截断，一条动辄 1500 字的"巨型事实"会把预算吃光
        #       （实测前 8 条就把额度用完，其余全被砍）。故对单条做软截断到 700 字，
        #       让同样预算能覆盖 3~4 倍的**不同**记忆。全文仍在 memory.db，用 memtether search 可取回。
        #    🔴 2026-09-15 三修：官方 MEMORY.md 槽位是**会话级注入口**，设计容量约 4000 字符。
        #       旧版把 73KB 投影塞进去 → 实测注入时被整体砍到只剩 4 条，且每条都是 700 字残片。
        #       **记得越多反而注入越少**。故改为「导航版」：每条只留首句结论（自含），
        #       总预算硬守官方限额；全文一律走 memtether search 按需取回。
        import re as _re
        _BUDGET = int(os.environ.get('MEM_PROJ_BUDGET', '3980'))
        #    🔴 2026-09-15 四修：CAP=60 实测 43/43 全部「不以句号结尾」——首句普遍超过 60 字，
        #       于是每条都被硬砍成半句话，连「首句要写自含结论」这条约定自己都被腰斩。
        #       模拟对比（active 全量）：CAP60→50条/12条硬砍，CAP100→42/5，CAP130→41/2，
        #       **CAP160→39条/0条硬砍（拐点）**，CAP200→38/0（收益递减）。
        #       取 160：少装 11 条换「条条完整」，因为半句话无法据以判断是否要取回全文。
        #       另：截断位置改为**回退到最近的句读**，避免出现「…决定中枢上限的四个硬约」这种断头。
        _LINE_CAP = 160

        # ★2026-09-26 十二修（类型感知压缩 · leanctx loss-tolerance routing 思路）：
        #   pin/decision 类是"高置信事实"，压坏结论等于丢事实 → 全量保留（cap=None）。
        #   experience 是过程叙事，信息密度最低 → 更激进截断（100），给高价值类型腾槽位。
        #   fact/incident 维持 160 拐点（四修实测该值能保证"条条完整首句"）。
        _LEAD_CAP = {'pin': None, 'decision': None, 'experience': 100}
        def _lead(txt, _cap=None):
            t = _re.sub(r'^【[^】]*】', '', (txt or '').strip()).strip()
            m = _re.search(r'[。；;！!？?\n]', t)
            _eff = _cap if _cap else _LINE_CAP
            # 2026-11-07 (mem0 P1-2): proper-noun protection. When trimming
            # mid-sentence (no sentence end within cap), find the last safe
            # cut point that does not land inside a quoted title, number,
            # or CamelCase identifier. Mirrors mem0 prompt's "Preserve proper
            # nouns and qualifiers" — a truncated proper noun is unfindable.
            def _safe_cut(seg, _target):
                """Walk back from _target to avoid cutting quoted/numeric/ident."""
                _OPEN = ('\u201c', '"', '\u300a', "'", '(')
                _CLOSE = ('\u201d', '"', '\u300b', "'", ')')
                for _p in '。；，、：;:！？':
                    _i = seg.rfind(_p, 0, _target)
                    if _i <= 0:
                        continue
                    # check if cut point is inside an open quote/bracket
                    _depth = 0
                    for _ch in seg[:_i + 1]:
                        if _ch in _OPEN:
                            _depth += 1
                        elif _ch in _CLOSE:
                            _depth = max(0, _depth - 1)
                    if _depth == 0:
                        # also check next char isn't a digit continuation
                        if _i + 1 < len(seg) and seg[_i + 1].isdigit() and _i > 0 and seg[_i - 1].isdigit():
                            continue
                        return _i
                return _target  # no safe boundary found, hard cut
            if m and 0 < m.start() < _eff:
                t = t[:m.start()].strip()
            elif len(t) > _eff:
                _use_cap = (_cap or _LINE_CAP); seg = t[:_use_cap]
                best = _safe_cut(seg, _use_cap)
                if best > _use_cap * 0.5:
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

        # ★2026-09-16 七修（★资产也要封顶）：实测 66 条资产里有 20 条带 ★，
        #   它们走 _prio 通道**不受任何上限约束**，一口气吃掉约 1000 字符 ——
        #   比事实区 15 条的全部篇幅还多。而资产的取用成本极低（一条 memtether search 就取到），
        #   把 4000 字符槽位的四分之一交给"软件安装路径"是本末倒置。
        #   故：★资产只保前 N 条（默认 6），其余按 name 序落回 _rest 一起竞争剩余名额。
        _asset_prio_max = int(os.environ.get('MEM_PROJ_ASSET_PRIO_MAX', '6'))
        _prio_kept = _prio[:_asset_prio_max]
        _rest = _prio[_asset_prio_max:] + _rest

        _tail.extend(_prio_kept)
        _room = _BUDGET - sum(len(x) + 1 for x in mem_lines) - sum(len(x) + 1 for x in _tail) - 90

        # ★2026-09-15 五修：实测投影 4020 字符 / 预算 4000 → **超 20 字符**。
        #   根因：页脚那行（"导航版：已列 N 条…"）是在**预算检查之后**才 append 的，
        #   它没有被任何 _room 约束覆盖；同时 _room 可能被前两项算成负数。
        #   而超预算的后果很严重：官方注入时**整体截断**，不是截掉末尾。
        #   修法：① 页脚预留固定额度；② 所有 _room 取值加 max(0, ...)；
        #        ③ 收尾做一次**硬裁**，确保 len(mem_text) <= _BUDGET。
        _FOOTER_RESERVE = 110
        _room = max(0, _room - _FOOTER_RESERVE)

        # ★2026-09-16 七修（资产区封顶）：实测资产区 19 条吃掉约 855 字符 ≈ 10 条事实的额度，
        #   而资产的取用成本极低（要用时一条 memtether search 就取到），信息密度远低于经验类事实。
        #   故给非优先资产**预留固定额度**，把省下的预算让给事实区。
        #   （此处必须"预留"而非"事后限制"：填充顺序是 事实 → 非优先资产，
        #     若不预留，事实填完时 _room 已耗尽，省下的空间只会变成投影尾部空白。）
        #   ★_asset_max 是**资产区总条数**上限（含上方已进的 ★ 资产），默认 12。
        _asset_max = int(os.environ.get('MEM_PROJ_ASSET_MAX', '12'))
        _asset_rest_max = max(0, _asset_max - len(_prio_kept))
        _avg_rest = (sum(len(x) + 1 for x in _rest) / len(_rest)) if _rest else 0
        _ASSET_RESERVE = int(_avg_rest * _asset_rest_max) + 40
        #   ★两个额度必须**分开**：旧版只有一个 _room，预留扣掉后就再也回不到资产区，
        #     资产区只能吃"事实区没花完的残渣"，封顶形同虚设。
        _room_assets = _ASSET_RESERVE
        _room = max(0, _room - _ASSET_RESERVE)

        # ★2026-09-16 六修（预算可观测化 + 类型保底）：
        #   ① 旧实现在预算不足时**静默 break** —— 读者只看到页脚"已列 16 条"，
        #      不知道还有 100+ 条被砍、更不知道被砍的是哪几类。而超预算的后果是
        #      官方注入侧**整体截断**（不是截掉末尾），属于"跑起来不报错、但结果全错"。
        #      故本版把所有裁剪动作**显式计数 + 落 stderr + 进返回结构**。
        #   ② 类型保底：防某一类被整体饿死。历史坑：更早的实现在 SQL 里写
        #      `type IN ('fact','decision') LIMIT 80`，把 incident/experience 全丢，
        #      而它们恰恰最该被记住（ACL 拒写坑、判病毒方法论、会话卡顿真因）。
        #      策略：预算不足时先保「每类最新 N 条」，再按时间倒序填其余；
        #      预算充足时行为与旧版**完全一致**（只是填充顺序不同，输出会重排回去）。
        _floor_n = int(os.environ.get('MEM_PROJ_TYPE_FLOOR', '2'))
        _floor_idx = []
        if _floor_n > 0:
            # ★2026-10-19：floor 选每类最短的 N 条（而非最新 N 条）——
            #   最新的往往 400+ chars，预算不足时全部跳过 = 类型饿死。
            #   选最短的保证至少有代表进投影。
            import re as _re_mod
            def _lead_len(_txt):
                t = _re_mod.sub(r'^【[^】]*】', '', (_txt or '').strip()).strip()
                m = _re_mod.search(r'[。；;！!？?\n]', t)
                if m and m.start() < 200:
                    return m.start()
                return min(len(t), 200)
            _by_type = {}
            for _i, _p in enumerate(_picked):
                _by_type.setdefault(_p[1], []).append(_i)
            for _typ, _idxs in _by_type.items():
                _sorted = sorted(_idxs, key=lambda _i: _lead_len(_picked[_i][2]['content']))
                _floor_idx.extend(_sorted[:_floor_n])
        _floor_set = set(_floor_idx)
        # ★pin 条目排在最前（先于类型保底）—— 它们是"必须一直在"的定义类结论。
        _pin_idx = [i for i, p in enumerate(_picked)
                    if 'pin' in [x.strip().lower() for x in (p[2]['tags'] or '').split(',') if x.strip()]]
        _pin_set = set(_pin_idx)
        # ★E2：NREM summary 排在 pin 之后
        _nrem_idx = [i for i, p in enumerate(_picked)
                     if 'nrem_consolidated' in (p[2]['tags'] or '').lower()]
        # ★2026-10-19：NREM cap — 74条NREM summary可吃掉全部预算导致类型饿死
        _NREM_CAP = 5
        if len(_nrem_idx) > _NREM_CAP:
            _nrem_idx = _nrem_idx[:_NREM_CAP]  # 已按时间倒序，取最新
        _nrem_set = set(_nrem_idx)
        # ★2026-11-04 reorder: floor BEFORE pins (mirror hub). Floor is the
        # anti-starvation guarantee; pins remain unconditional right after.
        _fill_order = [(i, _picked[i]) for i in _floor_idx
                        if i not in _pin_set]
        _fill_order += [(i, _picked[i]) for i in _pin_idx]
        # ★R-today: same-day rows follow pins, mirroring hub.
        _today_idx = [i for i, p in enumerate(_picked)
                      if str(p[2]['created_at'] or '')[:10] == _today_str]
        _today_set = set(_today_idx)
        _fill_order += [(i, _picked[i]) for i in _today_idx if i not in _pin_set]
        _fill_order += [(i, _picked[i]) for i in _nrem_idx if i not in _pin_set]
        # ★2026-09-23 十修（新闻带内短条优先）：纯全局短优会把类型比例打乱（实测 fact9/dec11），
        #   纯逐条短优则会丢掉最新条目（实测最新 2 条缺 2 条）。
        #   折中：每一类内部先切成“新闻带”（默认每 25 条一带，带内按时间倒序索引切），
        #   带内按 lead 长度升序；带与带之间保持新→旧。带宽越大越偏向“短优”。
        #   ★真实代码扫描（同一份 405 条 active 库，floor=2，含页眉/资产/页脚）：
        #     recency(回退) → 22 条 | 半句 6（= 线上原始现状）
        #     带宽 5/8/10   → 23~24 条 | 半句 4
        #     带宽 12/15    → 25 条 | 半句 4（类型 9/7/5/4）
        #     带宽 20       → 25 条 | 半句 4
        #     带宽 25/30/40 → 26 条 | 半句 4（类型 9/8/5/4）← 采纳 25
        #   ★保留 2026-09-16 七修本意：类型保底在最前，各类至少进 2 条（floor=2）。
        #   ★回退开关：MEM_PROJ_FILL=recency 恢复纯时间倒序（旧行为）。
        _fill_mode = (os.environ.get('MEM_PROJ_FILL') or 'band').strip().lower()
        _band = max(1, int(os.environ.get('MEM_PROJ_BAND', '25')))
        _rest_by_typ = {}
        for _i, _p in enumerate(_picked):
            if _i in _pin_set or _i in _floor_set or _i in _today_set:
                continue
            _rest_by_typ.setdefault(_p[1], []).append((_i, _p))
        _bucket = {}
        for _t, _lst in _rest_by_typ.items():
            if _fill_mode == 'recency':
                _bucket[_t] = list(_lst)
            else:
                _idxed = list(enumerate(_lst))
                # ★2026-09-25 十一修（Q-Value 槽位优先）：
                #   auto-reinforce 积累的使用信号（q_value / use_count）此前只影响
                #   检索排序（memsearch.py），投影选择完全忽略——高价值条目仍被
                #   带内 lead 长度挤掉。借鉴 context-engine 槽位分级 + leanctx
                #   loss-tolerance routing，在带内加 q_value 降序因子：
                #   band 不变（保持新→旧），带内先按 q_value 降序、再按 lead 长度升序。
                #   效果：被实际检索过的高 Q 条目优先进 3980 槽位，实现"用得越多越能留"。
                #   回退：q_value 相同（默认 0.5）时退化为十修的短条优先，零行为差异。
                _bucket[_t] = [
                    _ip for _rank, _ip in sorted(
                        _idxed,
                        key=lambda _rv: (_rv[0] // _band,
                                        -float(_rv[1][1][2]['q_value'] or 0.5),
                                        len(_lead(_rv[1][1][2]['content'], _LEAD_CAP.get(_rv[1][1][2]['type'], _LINE_CAP))),
                                        _rv[0]))
                ]
        _k = 0
        while True:
            _hit = False
            for _t, _q in _QUOTA:
                _lst = _bucket.get(_t) or []
                if _k < len(_lst):
                    _fill_order.append(_lst[_k])
                    _hit = True
            if not _hit:
                break
            _k += 1
        # 兜底：_pin/_floor/上方未覆盖到的条目（防将来新增类型被整体丢弃）
        _covered = {i for i, _p in _fill_order}
        _fill_order += [(i, p) for i, p in enumerate(_picked) if i not in _covered]
        _kept = 0
        _skipped_facts = 0
        _dropped_facts = 0
        _kept_pairs = []
        _type_kept = {}
        # 2026-11-04: starvation is hard to debug from warnings alone; dump the
        # skip decisions behind an opt-in env (zero cost when unset).
        _dbg = bool(os.environ.get('MEM_PROJ_DEBUG'))
        if _dbg:
            sys.stderr.write('[proj-debug] fill_order=%d room0=%s\n' % (len(_fill_order), _room))
        # 2026-11-04: pins must never starve the floor. 10 pins x 200-char leads
        # ate the entire budget before floor items ran (prod trace: room=27 by the
        # time floor items arrived -> every floor row skipped -> starved-type alarm).
        # Bound each pin's lead so unconditional inclusion stays affordable.
        _PIN_LEAD_CAP = int(os.environ.get('MEM_PROJ_PIN_LEAD_CAP', '140'))
        _FLOOR_LEAD_CAP = int(os.environ.get('MEM_PROJ_FLOOR_LEAD_CAP', '90'))
        # 2026-11-07 (Persome P1#10): render all candidate lines first, then
        # bin-pack: with the old greedy "skip if it doesn't fit" walk, a huge
        # candidate early in the order could leave headroom that later short
        # candidates never reclaimed (prod trace: room=624 wasted). First-fit
        # over pre-rendered lines = minimal fits chosen globally, not by luck
        # of order. Opt-out: MEM_PROJ_GREEDY_FILL=1 restores the old walk.
        if os.environ.get('MEM_PROJ_GREEDY_FILL') != '1':
            _cand = []
            for _i, (_ts, _typ, _r) in _fill_order:
                _d = (_r['updated_at'] or _r['created_at'] or '')[:10] or '????-??-??'
                _is_pin = _i in _pin_set
                _is_floor = _i in _floor_set
                _lead_txt = _lead(_r['content'])
                if _is_pin and _PIN_LEAD_CAP and len(_lead_txt) > _PIN_LEAD_CAP:
                    _lead_txt = _lead_txt[:_PIN_LEAD_CAP].rstrip() + '…'
                elif _is_floor and _FLOOR_LEAD_CAP and len(_lead_txt) > _FLOOR_LEAD_CAP:
                    _lead_txt = _lead_txt[:_FLOOR_LEAD_CAP].rstrip() + '…'
                _item = _hg_fact_line(_d, _typ, _r['source'], _lead_txt)
                _cand.append((_i, _typ, _item))
            # shortest-first bin packing, but FLOOR ROWS ALWAYS WIN first:
            # pure length order let 10 long non-floor rows starve the 'floor'
            # type guarantee (prod: procedure type zero). Floor-first keeps the
            # anti-starvation contract; then shortest-first packs the rest
            # (Persome P1#10).
            _cand.sort(key=lambda c: (c[0] not in _floor_set, len(c[2])))
            # type diversity after floor: reserve at least one more slot per
            # type before letting one type hog the tail (prod: procedure starved
            # again under pure length order even with floor=2)
            _type_count = {}
            _cand_sorted = []
            _deferred = []
            for _ci, (_i, _typ, _item) in enumerate(_cand):
                if _i in _floor_set:
                    _cand_sorted.append((_i, _typ, _item))
                    _type_count[_typ] = _type_count.get(_typ, 0) + 1
                else:
                    _deferred.append((_i, _typ, _item))
            _min_type = int(os.environ.get('MEM_PROJ_TYPE_MIN', '2'))
            if _min_type > 1:
                _promoted = []
                _rest_deferred = []
                for _d in _deferred:
                    _i, _typ, _item = _d
                    if _type_count.get(_typ, 0) < _min_type:
                        _promoted.append(_d)
                        _type_count[_typ] = _type_count.get(_typ, 0) + 1
                    else:
                        _rest_deferred.append(_d)
                _rest_deferred.sort(key=lambda c: len(c[2]))
                _cand_sorted.extend(_promoted)
                _cand_sorted.extend(_rest_deferred)
            else:
                _deferred.sort(key=lambda c: len(c[2]))
                _cand_sorted.extend(_deferred)
            _cand = _cand_sorted
            for _i, _typ, _item in _cand:
                if len(_item) + 1 > _room:
                    _skipped_facts += 1
                    continue
                mem_lines.append(_item)
                _kept_pairs.append((_i, _item))
                _type_kept[_typ] = _type_kept.get(_typ, 0) + 1
                _room -= len(_item) + 1
                _kept += 1
        else:
            for _i, (_ts, _typ, _r) in _fill_order:
                _d = (_r['updated_at'] or _r['created_at'] or '')[:10] or '????-??-??'
                _is_pin = _i in _pin_set
                _is_floor = _i in _floor_set
                _lead_txt = _lead(_r['content'])
                if _is_pin and _PIN_LEAD_CAP and len(_lead_txt) > _PIN_LEAD_CAP:
                    _lead_txt = _lead_txt[:_PIN_LEAD_CAP].rstrip() + '…'
                elif _is_floor and _FLOOR_LEAD_CAP and len(_lead_txt) > _FLOOR_LEAD_CAP:
                    _lead_txt = _lead_txt[:_FLOOR_LEAD_CAP].rstrip() + '…'
                _item = _hg_fact_line(_d, _typ, _r['source'], _lead_txt)
                if len(_item) + 1 > _room:
                    if _dbg:
                        sys.stderr.write('[proj-debug] SKIP %s len=%d room=%d :: %s\n'
                                         % (_typ, len(_item)+1, _room, _item[:60]))
                    _skipped_facts += 1
                    continue
                mem_lines.append(_item)
                _kept_pairs.append((_i, _item))
                _type_kept[_typ] = _type_kept.get(_typ, 0) + 1
                _room -= len(_item) + 1
                _kept += 1
        _dropped_facts = len(_picked) - _kept
        # ★事实区没花完的额度转给资产区（避免"事实装不下、资产也空着"的双重浪费）
        _room_assets += _room
        # ★输出必须恢复「新→旧」约定：注入侧按体积截断时只会丢最老的。
        #   排序键是 _picked 的原始下标（_picked 已按时间倒序），**不能按整行字符串排** ——
        #   同一天写入的条目日期前缀相同，整行降序会退化成「按内容字典序」，
        #   使顺序与真实时间脱钩（2026-09-16 实测踩到：集合未变、字符数未变，
        #   但行序全乱，属于"跑起来不报错、但结果错"的一类）。
        #   （_kept == 0 时切片会误伤页眉，必须判空）
        if _kept:
            mem_lines[-_kept:] = [_it for _i, _it in sorted(_kept_pairs)]

        # 事实填完后，把剩余预算给非优先资产
        _extra = []
        _dropped_assets = 0
        _rest_in = 0
        for _i, _line in enumerate(_rest):
            if _i >= _asset_rest_max or len(_line) + 1 > _room_assets:
                # ★注意：_extra 末尾那行"…另 N 条"是**提示行不是资产**，
                #   计数必须用 _rest_in，不能用 len(_extra)（差一，2026-09-16 实测踩到：
                #   告警报"已进 13 条"而实际只有 12 条）。
                _dropped_assets = len(_rest) - _rest_in
                _extra.append('- …另 %d 条资产见网关库（memtether search 或 tool_audit.py audit）'
                              % _dropped_assets)
                break
            _extra.append(_line)
            _rest_in += 1
            _room_assets -= len(_line) + 1
        _tail.extend(_extra)

        mem_lines.extend(_tail)
        mem_lines.append('')
        mem_lines.append('> 导航版：已列 %d 条 / active 共 %d 条（受官方 4000 字符槽位硬限，'
                        '写多会被整体截断）；其余全文用 memtether search 取。'
                        % (_kept, sum(len(v) for v in sink.values())))
        mem_text = '\n'.join(mem_lines)

        # ★硬裁保险：无论上游怎么算，最终产物绝不超过 _BUDGET。
        #   超了就从**事实区**末尾往回删整行（保留页脚，因为它告知读者"还有更多"）。
        #   ★2026-09-16：硬裁本身是"上游算错了"的信号 —— 触发即报警，不再静默吞掉。
        _hard_trimmed = 0
        _hard_clipped = False
        if len(mem_text) > _BUDGET:
            _foot = mem_lines[-2:]
            _body = mem_lines[:-2]
            while _body and len('\n'.join(_body + _foot)) > _BUDGET:
                _body.pop()
                _hard_trimmed += 1
            mem_text = '\n'.join(_body + _foot)
            if len(mem_text) > _BUDGET:      # 极端情况：连页脚都放不下，砍到纯粹硬上限
                mem_text = mem_text[:_BUDGET]
                _hard_clipped = True

        # ★2026-09-17：改走原子写（问题③）。原 `open(wb,'w')` 有两个隐患：
        #   ① 非原子：写到一半被打断 → 投影半截，而注入侧读到的就是半截；
        #   ② 换行风格：`open(p,'w')` 默认 newline=None 会把 '\n' 翻成 os.linesep（Windows=CRLF），
        #      线上投影现在是 CRLF。hubguard.atomic_write 默认 `_detect_newline()` **跟随现状**，
        #      不会出现"内容没变、字节全变"的假 diff。
        # ★目标列表：不设 MEM_PROJ_PATH 时与原来那一个硬编码路径**完全相同**（行为不变）；
        #   设了就写到隔离路径 —— 否则"想验证 rebuild 的改动"就必然要动线上投影，等于不能安全地测。
        _wb_targets = _proj_targets()
        if not _wb_targets:
            # a75-r7: no WorkBuddy projection targets (open-source / CI env).
            # Skip the WorkBuddy projection instead of crashing — sink.json
            # still gets written above, so rebuild() remains useful.
            conn.commit()
            conn.close()
            # 2026-11-06 (Persome audit P0#4): fail-soft returns carry a health
            # block so 'skipped' is never mistaken for 'verified healthy'.
            _hconn = get_conn()
            try:
                _hc = _hconn.execute("SELECT COUNT(*) FROM facts WHERE status='active'").fetchone()[0]
                _hf = _hconn.execute("SELECT COUNT(*) FROM facts_fts").fetchone()[0]
            finally:
                _hconn.close()
            _hv = 0
            try:
                import memsearch as _ms
                _hv = _ms._client().get_collection(_ms.COLLECTION).count()
            except Exception:
                pass
            return {'ok': True, 'op': 'rebuild', 'skipped': 'no_wb_targets',
                    'note': 'no projection targets detected; sink.json only',
                    'health': {'active_count': _hc, 'fts_count': _hf,
                               'vector_count': _hv, 'schema_ok': True}}
        wb = _wb_targets[0]
        # ★2026-09-17（问题③）：`MEM_PROJ_PATH` 是"我明确要求写到隔离路径"的信号。
        #   没设它 ⇒ 目标就是**线上共享投影**（两个实例共写同一 inode）。
        _redirected = bool(os.environ.get('MEM_PROJ_PATH'))
        _writes = []
        _refused = []
        for _t in _wb_targets:
            # ★fail-closed：没有守卫却要写**共享槽位** ⇒ 拒写，绝不裸写。
            #   原实现在 `HG is None` 时退化成 `open(w,'w')` —— 等于"最需要保护的时刻
            #   （守卫缺失）反而毫无保护"，而且**完全静默**。
            #   2026-09-17 实测踩到过它的另一面：演练里把 HG 置 None，结果直接覆盖了线上投影。
            #   故：只有调用方**显式**指定了隔离路径（MEM_PROJ_PATH）才允许无守卫落盘。
            if HG is None and not _redirected:
                _why = ('无 hubguard 且目标是共享投影（未设 MEM_PROJ_PATH）—— '
                        '拒写，以免静默覆盖另一实例的更新')
                _refused.append({'kind': 'no-guard', 'path': _t, 'why': _why})
                _writes.append({'path': _t, 'refused': True, 'why': _why})
                sys.stderr.write('[rebuild][warn] ★拒绝写入共享投影 %s：%s\n' % (_t, _why))
                continue
            os.makedirs(os.path.dirname(_t), exist_ok=True)
            # ★并发判据的基准必须在**读之前**取：先 snapshot，再读 _old。
            #   反过来（先读 _old 再 snapshot）会把"我读 _old 与 snapshot 之间"发生的
            #   改动算成"没变" —— 冲突检测被自己的读取顺序抹掉，变成假通过。
            _before = HG.snapshot(_t) if HG is not None else None
            # ★问题③/②回归闸门（2026-09-17）：
            #   投影是**两个实例共写的同一物理文件**（实测 .workbuddy 与 .workbuddy-ai
            #   是同一 inode），而两侧 gateway.py **不同源**。所以"修好来源标记"这件事
            #   随时可能被另一侧的一次 rebuild 静默抹掉 —— 谁最后跑谁说了算。
            #   判据：磁盘上现有投影**有**来源标记，而本次产物**没有** ⇒ 本次生成器更旧。
            #   处置：照写（不写会让投影变陈旧，更坏），但把对方版本另存 + 大声报警。
            _downgrade = None
            if os.path.exists(_t):
                try:
                    with open(_t, encoding='utf-8') as _f:
                        _old = _f.read()
                    _n_old, _n_new = _count_tagged(_old), _count_tagged(mem_text)
                    if _n_old and not _n_new:
                        _downgrade = _n_old
                        _side = '%s.tagged-%s' % (_t, time.strftime('%Y%m%d-%H%M%S'))
                        _hg_write(_side, _old)
                        sys.stderr.write(
                            '[rebuild][warn] ★★格式回退：磁盘上现有投影有 %d 条来源标记，'
                            '本次产物 0 条 —— 说明本次生成器更旧（多半是没装 hubguard.py）。'
                            '已把对方版本另存为 %s\n' % (_n_old, _side))
                except Exception as _e:
                    sys.stderr.write('[rebuild][warn] 格式回退检查跳过：%s\n' % _e)
            _cm = getattr(HG, 'ConcurrentModification', None) if HG is not None else None
            try:
                _r = _hg_write(_t, mem_text, before=_before,
                               on_conflict='abort', tag='gateway.rebuild')
            except Exception as _e:
                if _cm is None or not isinstance(_e, _cm):
                    raise
                # 对方在我们"读投影"之后写了一次。对方那版**同样是刚生成的有效投影**
                # （同一份库、同一时刻），所以这不是故障而是**保护生效**：本次让位、不覆盖。
                # 投影因此不会变陈旧；万一对方写的是更旧的格式，下一次 rebuild 的
                # "格式回退闸门"会照旧把它另存并重写 —— 可自愈，故不做重试（避免活锁）。
                _why = ('另一实例在本次 rebuild 的"读—写"之间更新了投影，本次让位'
                        '（对方版本已落盘；投影未变陈旧，无需重试）')
                _refused.append({'kind': 'concurrent', 'path': _t, 'why': _why,
                                 'detail': str(_e)})
                _writes.append({'path': _t, 'refused': True, 'why': _why})
                sys.stderr.write('[rebuild][warn] ★并发让位 %s：%s\n%s\n' % (_t, _why, _e))
                continue
            _w = {k: _r.get(k) for k in ('path', 'bytes', 'atomic', 'newline', 'why',
                                        'symlink', 'hardlink', 'real')
                  if _r.get(k) is not None}
            if _downgrade:
                _w['format_downgrade_from'] = _downgrade
            _writes.append(_w)

        # ★2026-09-16 六修：把"被砍了多少 / 哪类被饿死 / 余量还剩多少"全部显式化。
        #   验收口径不是"跑通了"，而是**故意制造超预算，看它报不报**。
        #   （此前是静默 _body.pop()，读者完全看不出投影已经被裁剪过。）
        _used = len(mem_text)
        _headroom = _BUDGET - _used
        _warnings = []
        if _hard_clipped:
            _warnings.append('★★严重：投影被**整体截断**（尾部半句已丢失）——'
                             '官方注入侧是按体积整体砍的，必须降低写入量或收紧 _LINE_CAP')
        if _hard_trimmed:
            _warnings.append('★硬裁触发：正文超出预算，已从末尾回删 %d 行'
                             '（事实区/资产区均可能被删；正常路径不该走到这里）' % _hard_trimmed)
        if _dropped_facts:
            _warnings.append('预算不足：%d 条事实未进投影（配额选出 %d 条 · 实入 %d 条）'
                             % (_dropped_facts, len(_picked), _kept))
        if _dropped_assets:
            _warnings.append('预算不足：%d 条资产未进投影' % _dropped_assets)
        # 2026-11-04: starvation is only meaningful when the type HAS active rows.
        # A type with zero active rows (procedure often) was reported starved on
        # every rebuild - a permanent false alarm that trains operators to
        # ignore the warning channel (the exact "wolf!" failure the six-fix
        # observability pass was meant to prevent).
        _active_by_type = {t: conn.execute(
            "SELECT COUNT(*) FROM facts WHERE status='active' AND type=?", (t,)).fetchone()[0]
            for t, _q in _QUOTA}
        _starved = [t for t, _q in _QUOTA
                    if not _type_kept.get(t) and _active_by_type.get(t, 0) > 0]
        if _kept == 0:
            # ★预算小到一条事实都放不下时，报"类型饿死"是**误报** ——
            #   饿死的根因是预算太小，不是配额设计问题，照旧给"调低 _QUOTA"的建议会误导。
            #   （2026-09-16 受控演练 T1/T2 实测踩到。）
            _warnings.append('★★预算过小：一条事实都放不下（预算 %d）—— '
                             '投影将只剩页眉/页脚，事实区为空，请检查 MEM_PROJ_BUDGET 是否被外部调小'
                             % _BUDGET)
        elif _starved:
            _warnings.append('★类型饿死：%s 一条都没进投影（请调低 _QUOTA 或收紧 _LINE_CAP）'
                             % ' / '.join(_starved))
        # ★2026-09-16 七修：跳过本身不算异常（额度用尽时必然发生），
        #   **跳过之后还剩大额余量**才是异常 —— 那说明剩余候选普遍超长、额度被浪费。
        if _skipped_facts and _room > 120:
            _warnings.append('★填充浪费：跳过 %d 条后事实区仍余 %d 字符'
                             '（剩余候选普遍超长，可考虑收紧 _LINE_CAP 或提高 _QUOTA）'
                             % (_skipped_facts, _room))
        #   ★2026-09-17 修：旧措辞"再写一条记忆就会触发裁剪"**是错的**：新记忆时间最新、
        #   必然入选，只会挤掉一条最老的入选条目，**总占用不变**，不触发 _hard_trimmed。
        #   真正的裁剪信号是 _hard_trimmed / _hard_clipped（上方已有独立告警）。
        #   故改为：只在"候选已全部装完、余量却仍很小"时才提示 ——
        #   那才是"预算刚好够用、再涨就要开始挤"的有意义信号。
        if _headroom < 100 and not _dropped_facts:
            _warnings.append('预算刚好够用：候选事实已全部装完，余量仅 %d 字符'
                             '（预算 %d）—— 下次新增内容将开始挤掉旧条目'
                             % (_headroom, _BUDGET))
        # ★2026-09-16 八修：pin 是**稀缺资源** —— 每条 pin 都无条件占位，
        #   代价是挤掉一条普通条目。无节制 pin 会让投影退化成"pin 版最近 N 条"，
        #   反而丢掉"最近发生了什么"。故设软上限并显式提示（只提醒，不阻止）。
        _pin_max = int(os.environ.get('MEM_PROJ_PIN_MAX', '10'))
        if len(_pin_rows) > _pin_max:
            _warnings.append('★pin 过多：已钉住 %d 条（软上限 %d）—— 每条 pin 都无条件'
                             '挤掉一条普通条目，投影会退化成"pin 版最近 N 条"；'
                             '释放：gateway.py pin <uid> --off'
                             % (len(_pin_rows), _pin_max))
        # ★2026-09-17：拒写/让位必须进**返回结构**，不能只写 stderr
        #   —— 否则调用方（mcp_server / 维护脚本）读到的仍是"一切正常"。
        for _r0 in _refused:
            if _r0.get('kind') == 'no-guard':
                _warnings.append('★★拒绝写入共享投影 %s：%s' % (_r0['path'], _r0['why']))
            else:
                _warnings.append('并发让位 %s：%s' % (_r0['path'], _r0['why']))
        for _w in _warnings:
            sys.stderr.write('[rebuild][warn] %s\n' % _w)

        conn.commit()
        # ★"无守卫拒写"是**硬失败**（没有任何一方写成功）⇒ ok=False，调用方能看出来；
        #   "并发让位"是软事件（对方已写好一版）⇒ ok 仍为 True，只进 warnings。
        _hard = [r for r in _refused if r.get('kind') == 'no-guard']
        return {'ok': not _hard, 'sink': SINK, 'mem': wb, 'facts': len(sink['fact']),
                'tools': len(conn.execute("SELECT id FROM tool_assets WHERE status='active'").fetchall()),
                # —— 预算可观测字段（2026-09-16 新增，供 hub_selfcheck / 维护脚本消费）
                'budget': _BUDGET, 'used': _used, 'headroom': _headroom,
                'facts_listed': _kept, 'type_listed': _type_kept,
                # ★2026-09-16 七修新增可观测字段：
                #   facts_skipped_toolong = 因单条超长被跳过（不是预算耗尽），
                #   assets_listed = 资产区实进条数（不含"…另 N 条"提示行）。
                'facts_skipped': _skipped_facts,
                # ★2026-09-16 八修：pin 条数可观测（供自检脚本判断"pin 是否被滥用"）
                'pinned_count': len(_pin_rows),
                'assets_listed': len(_prio_kept) + _rest_in,
                'dropped_facts': _dropped_facts, 'dropped_assets': _dropped_assets,
                'hard_trimmed': _hard_trimmed, 'hard_clipped': _hard_clipped,
                'warnings': _warnings,
                # —— 并发治理可观测字段（2026-09-17 新增）
                #   'writes' 里 atomic=False 表示 os.replace 被拒/目标有硬链接而降级就地写，
                #   调用方**必须**把这个字段报出去，否则又是"跑起来不报错但结果错"。
                'writes': _writes, 'guarded': bool(HG is not None),
                'refused': _refused}
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


def _load_governance():
    r"""★2026-09-22：按**显式文件路径**加载 governance，杜绝同名模块遮蔽。

    手册卷12 事故 #10 的遗留半边：hubguard 已改用 spec_from_file_location 修好，
    但本文件的 govern/conflicts/conflicts_exact/selfcheck/stale 五个分支一直是
    裸 `import governance`。仓库外若有一份同名 governance.py 且硬编码了别的
    memory.db（不认 MEM_DB），一旦它先进入 sys.path 就连到空库上，
    报 no such table: facts，而 rebuild/remember 全不报错 —— 静默回归。
    与 hubguard 同一套修法：按路径加载，不给模块名解析留机会。
    """
    import importlib.util as _iu
    _p = os.path.join(HUB, 'governance.py')
    if not os.path.isfile(_p):
        return __import__('governance')            # 兜底：走普通搜索路径
    _spec = _iu.spec_from_file_location('_mt_governance', _p)
    _mod = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    # a74-fix: governance.py hardcodes DB = HERE/memory.db and ignores MEM_DB.
    # Without this patch the five governance CLI branches (govern/conflicts/
    # conflicts_exact/selfcheck/stale) silently read the placeholder repo DB
    # while remember/rebuild read the MEM_DB — one CLI session, two databases.
    _mod.DB = DB
    return _mod


def _fix_stdio():
    """★2026-09-22：stdout/stderr 按 UTF-8 重配（errors=replace）。

    Windows 控制台默认 GBK，而库内可能含 GBK 编不出的字符（如 🦞）。
    实测只读视图（`gateway.py qvalue` 不带参数）会直接 UnicodeEncodeError 崩掉，
    连它自己 docstring 建议的验收命令都跑不通。
    """
    for _name in ('stdout', 'stderr'):
        _s = getattr(sys, _name, None)
        try:
            if _s is not None and hasattr(_s, 'reconfigure'):
                _s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def main():
    _fix_stdio()
    _env_guard()
    ap = argparse.ArgumentParser(description='Memory Gateway — 三 agent 唯一记忆入口')
    sub = ap.add_subparsers(dest='cmd')

    r = sub.add_parser('remember'); r.add_argument('content'); r.add_argument('--type', default='fact')
    r.add_argument('--source', default=DEFAULT_SOURCE); r.add_argument('--scope', default='shared')
    r.add_argument('--subject', default='user'); r.add_argument('--tags', default='')

    s = sub.add_parser('search'); s.add_argument('query'); s.add_argument('--limit', type=int, default=10)
    s.add_argument('--mem0', action='store_true')

    g = sub.add_parser('get'); g.add_argument('uid')

    c = sub.add_parser('correct'); c.add_argument('old_uid'); c.add_argument('new_content'); c.add_argument('--reason', default='')

    rt = sub.add_parser('retire'); rt.add_argument('uid'); rt.add_argument('--reason', default='')
    # ★2026-09-22 修：此前 retire 连 --source 都没有，by_agent 恒为 DEFAULT_SOURCE，
    #   无法追溯是谁退役的；且函数体内漏了 _guard_source（remember/correct/record_tool 都有）。
    rt.add_argument('--source', default=DEFAULT_SOURCE)

    t = sub.add_parser('record_tool'); t.add_argument('name'); t.add_argument('--path'); t.add_argument('--entrypoint')
    t.add_argument('--aliases', default=''); t.add_argument('--type', default='local_tool')
    t.add_argument('--capabilities', default=''); t.add_argument('--known_failures', default='[]')
    # ★2026-09-22 修（与 memory_hub 同源修复）：此前没有 --source，
    #   分发处只能落 DEFAULT_SOURCE ⇒ 任何 agent 经 CLI 记工具都可能记错归属。
    t.add_argument('--source', default=DEFAULT_SOURCE)

    rk = sub.add_parser('resolve_task'); rk.add_argument('task')

    # P0-2 (2026-10-18): recipes 写入 API（此前只能手工 SQL）
    rc = sub.add_parser('record_recipe'); rc.add_argument('task_patterns')
    rc.add_argument('--steps', required=True, help='JSON 数组，每步一条命令')
    rc.add_argument('--preferred-tool', default=None)
    rc.add_argument('--output-paths', default=None, help='JSON 数组')
    rc.add_argument('--known-pitfalls', default=None, help='JSON 数组')
    rc.add_argument('--fallback-tools', default=None, help='JSON 数组')
    rc.add_argument('--source', default=DEFAULT_SOURCE)

    # 记忆自动闭环
    ev = sub.add_parser('event'); ev.add_argument('--type', required=True); ev.add_argument('--run-id', required=True)
    ev.add_argument('--agent', default=None); ev.add_argument('--payload-file', required=True)

    ar = sub.add_parser('auto_reflect'); ar.add_argument('--run-id'); ar.add_argument('--payload-file')

    pe = sub.add_parser('process_events'); pe.add_argument('--limit', type=int, default=10)

    ic = sub.add_parser('incident')
    ic.add_argument('--step', required=True); ic.add_argument('--error', required=True)
    ic.add_argument('--workaround', default=''); ic.add_argument('--result', default='')
    ic.add_argument('--run-id', default=None); ic.add_argument('--agent', default=DEFAULT_SOURCE)

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

    # ★投影钉住（见 set_pin）：把"必须一直在"的定义类结论排除在时间竞争之外
    pn = sub.add_parser('pin')
    pn.add_argument('uid', nargs='?')              # 省略 = 列出当前已钉住
    pn.add_argument('--off', action='store_true')  # 取消钉住

    # ★升级 1（Q-Value，见 bump_qvalue）：记录"哪条记忆真的被采纳过"
    qv = sub.add_parser('qvalue')
    qv.add_argument('uid', nargs='?')              # 省略 = 只读看分布
    qv.add_argument('--reward', type=float, default=1.0)   # 1=完全采纳 0.5=部分有用 0=没用
    # ★2026-09-22 修：AGENTS.md/HARD-RULES 全家约定是 --source，原先只有 --agent
    #   ⇒ 照铁律写 `qvalue <uid> --source X` 会被 argparse 打回 exit 2。
    qv.add_argument('--source', '--agent', dest='agent', default=DEFAULT_SOURCE)  # 谁回写的（进 audit_log）
    qv.add_argument('--detail', default='')                # 回写原因（进 audit_log）
    qv.add_argument('--dry-run', action='store_true')      # 只算不写

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
        print(json.dumps(retire(a.uid, a.reason, by_agent=a.source), ensure_ascii=False))
    elif a.cmd == 'record_tool':
        print(json.dumps(record_tool(a.name, a.path, a.entrypoint, a.aliases, a.type,
                                     a.capabilities, a.known_failures, source=a.source), ensure_ascii=False))
    elif a.cmd == 'resolve_task':
        print(json.dumps(resolve_task(a.task), ensure_ascii=False))
    elif a.cmd == 'record_recipe':
        try:
            _steps = json.loads(a.steps)
        except Exception as e:
            print(json.dumps({'ok': False, 'error': '--steps must be JSON array: %s' % e}))
            sys.exit(1)
        _jp = lambda s: (json.loads(s) if s else None)
        print(json.dumps(record_recipe(
            a.task_patterns, _steps,
            preferred_tool=a.preferred_tool,
            output_paths=_jp(a.output_paths),
            known_pitfalls=_jp(a.known_pitfalls),
            fallback_tools=_jp(a.fallback_tools),
            source=a.source), ensure_ascii=False))
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
    elif a.cmd == 'pin':
        print(json.dumps(set_pin(a.uid, not a.off), ensure_ascii=False))
    elif a.cmd == 'qvalue':
        print(json.dumps(bump_qvalue(a.uid, a.reward, a.agent, a.detail, not a.dry_run),
                         ensure_ascii=False))
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
        governance = _load_governance()
        governance.audit()
    elif a.cmd == 'conflicts':
        governance = _load_governance()
        r = governance.find_conflicts(days_window=a.days)
        print(json.dumps({'ok': True, 'count': len(r), 'candidates': [
            {'a': x['a']['uid'], 'b': x['b']['uid'], 'shared': x['shared'],
             'a_text': x['a']['text'][:120], 'b_text': x['b']['text'][:120]}
            for x in r[:20]]}, ensure_ascii=False, indent=2))
    elif a.cmd == 'conflicts_exact':
        governance = _load_governance()
        r = governance.detect_explicit_conflicts()
        print(json.dumps({'ok': True, 'count': len(r), 'conflicts': [
            {'entity': x['entity'], 'newer': x['newer'],
             'suggest_retire': x['suggest_retire'], 'keep': x['keep'],
             'pos_text': x['pos']['sent'][:150], 'pos_ts': x['pos']['ts'],
             'neg_text': x['neg']['sent'][:150], 'neg_ts': x['neg']['ts']}
            for x in r]}, ensure_ascii=False, indent=2))
    elif a.cmd == 'selfcheck':
        governance = _load_governance()
        r = governance.find_self_contradictions()
        print(json.dumps({'ok': True, 'count': len(r), 'items': [
            {'uid': x['uid'], 'entity': x.get('entity'), 'shared': x['shared'],
             'pos': x['pos']['text'][:120], 'neg': x['neg']['text'][:120]}
            for x in r[:20]]}, ensure_ascii=False, indent=2))
    elif a.cmd == 'stale':
        governance = _load_governance()
        r = governance.find_stale(days=30)
        print(json.dumps({'ok': True, 'count': len(r), 'items': r[:20]},
                         ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 并发守卫装配（问题①）
#   ★放在这里、而不是改函数体：本机两个 gateway.py 版本不同源，逐行补丁对不齐；
#     包装器只依赖"函数名存在"，对两版都适用。
#   ★为什么模块级就装：任何 `import gateway` 的调用方（mem.py / tool_audit.py /
#     另一个客户端）拿到的是**同一个** module 对象，装配一次即全局生效。
#   ★为什么放在文件末尾：必须在所有 def 之后，否则包装会被后面的 def 覆盖掉。
#   ★同进程可重入是**刻意的**：否则 rebuild 内部再调 remember 会自锁死。
_HG_GUARDED = HG.install_guards(globals()) if HG is not None else []


if __name__ == '__main__':
    main()
