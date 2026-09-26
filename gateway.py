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
        return HG.atomic_write(path, text)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return {'ok': True, 'path': path, 'bytes': os.path.getsize(path), 'atomic': False}


HUB = os.path.dirname(os.path.abspath(__file__))
# ★真源库路径可被 MEM_DB 覆盖（默认仍是 HUB/memory.db，不设环境变量时行为完全不变）。
#   用途：仓库不发布真实 memory.db（含真名/学号/本机路径/密钥，见 .gitignore），
#   但开源版需要「clone 下来就能跑」——指向合成演示库即可：
#       MEM_DB=demo/memory_demo.db python mem.py search "演示查询"
#   演示库由 scripts/make_demo_db.py 生成（固定随机种子，逐字节可复现，零真实串）。
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
  use_count INTEGER DEFAULT 0 -- ★升级 1：被采纳次数（纯计数，不参与打分）
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


def _ensure_columns(conn):
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
    for name, typ, dflt in (('q_value', 'REAL', '0.5'), ('use_count', 'INTEGER', '0')):
        if name in have:
            continue
        conn.execute('ALTER TABLE facts ADD COLUMN %s %s DEFAULT %s' % (name, typ, dflt))
    # ★2026-09-20 扩展：tool_assets 也补 —— 资产条目占检索结果近半，
    #   没有这两列就永远吃不到 Q-Value 加权（bump 也无对象）。
    try:
        have_a = {r[1] for r in conn.execute('PRAGMA table_info(tool_assets)')}
    except sqlite3.Error:
        return
    for name, typ, dflt in (('q_value', 'REAL', '0.5'), ('use_count', 'INTEGER', '0')):
        if name in have_a:
            continue
        conn.execute('ALTER TABLE tool_assets ADD COLUMN %s %s DEFAULT %s'
                     % (name, typ, dflt))


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
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


def _vec_upsert(uid, content, type_='fact', source=DEFAULT_SOURCE):
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


def remember(content, type='fact', source=DEFAULT_SOURCE, scope='shared', subject='user',
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
    source = _guard_source(source)
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


def correct(old_uid, new_content, reason, by_agent=DEFAULT_SOURCE, valid_from=None):
    """用户纠正：旧事实 supersede，新事实 active。

    双时间轴处理：
      旧事实 valid_to = 新事实 valid_from（T 轴连续——旧事实止于新事实起，
        不留下"两边都不成立"的时间空档）
      旧事实 invalidated_at = 当前时刻（T' 轴——**我们此刻**才判定它失效，
        可能与 valid_to 不同；若纠正的是陈年旧事，两个值会明显分离）
    """
    by_agent = _guard_source(by_agent)
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
        # ★2026-09-24：旧行 supersede 后清 pin（同 memory_hub）
        conn.execute("UPDATE facts SET status='superseded', superseded_by=?, valid_to=?, "
                     "invalidated_at=?, updated_at=?, tags=(CASE WHEN lower(COALESCE(tags,'')) LIKE '%pin%' "
                     "THEN trim(replace(replace(',' || tags || ',', ',pin,', ','), ',PIN,', ','), ',') ELSE tags END) "
                     "WHERE uid=?",
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
                     "updated_at=?, tags=(CASE WHEN lower(COALESCE(tags,'')) LIKE '%pin%' "
                     "THEN trim(replace(replace(',' || tags || ',', ',pin,', ','), ',PIN,', ','), ',') ELSE tags END) "
                     "WHERE uid=?",
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
                capabilities='', known_failures='[]', prerequisites='', source=DEFAULT_SOURCE):
    source = _guard_source(source)
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
                "WHERE status='active' AND lower(tags) LIKE '%pin%' "
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
        #    ★2026-09-17：投影行加「来源」标记（问题②）。图例只在有 hubguard 时加，
        #      避免"标了来源却没解释"和"没标来源却留图例"两种半吊子状态。
        _legend = ('- 行首标记 `类型·来源`；来源 w=国内版 · a=国际版 · o=OpenClaw · d=豆包。'
                   if HG is not None else None)
        mem_lines = [
            '# MEMORY.md — 记忆中枢投影（导航版）',
            '<!-- 真源：%s；由 gateway.py rebuild 生成，勿手改 -->' % os.path.join(HUB, 'memory.db'),
            '',
            '## 怎么用',
            '- 取全文：cd %s && python mem.py search "<关键词>"' % HUB.replace('\\', '/'),
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

        # ★2026-09-16 七修（pin 钉住）：tags 含 pin 的条目**无条件**进入投影。
        #   根因：投影本质是「最近 N 条」而非「最重要 N 条」—— 新写的 experience/incident
        #   会把早先写下的定义类 fact 挤出槽位，表现为**同一个概念反复记不住**
        #   （实测痛点：同一个定义类概念，连问三次仍答不上来）。而"定义"恰恰最不该随时间沉底。
        #   机制：不新增 schema，复用既有 tags 字段（逗号分隔）。
        #     写入侧：python gateway.py remember "…" --tags pin
        #     选取侧：先按配额捞，再把 pin 条目**补进**（防其因配额上限/时间序被排除）。
        #   注意：这里只负责"选中"，不负责"排在前面"—— 排序交给下方 _fill_order。
        _pin_rows = conn.execute(
            "SELECT * FROM facts WHERE status='active' AND lower(tags) LIKE '%pin%'"
        ).fetchall()
        _have_uid = {p[2]['uid'] for p in _picked}
        for _r in _pin_rows:
            if _r['uid'] not in _have_uid:
                _picked.append((_r['updated_at'] or _r['created_at'] or '',
                                _r['type'] if _r['type'] in sink else 'fact', _r))
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

        # ★2026-09-26 十二修（类型感知压缩 · leanctx loss-tolerance routing 思路）：
        #   pin/decision 类是"高置信事实"，压坏结论等于丢事实 → 全量保留（cap=None）。
        #   experience 是过程叙事，信息密度最低 → 更激进截断（100），给高价值类型腾槽位。
        #   fact/incident 维持 160 拐点（四修实测该值能保证"条条完整首句"）。
        _LEAD_CAP = {'pin': None, 'decision': None, 'experience': 100}
        def _lead(txt, _cap=None):
            t = _re.sub(r'^【[^】]*】', '', (txt or '').strip()).strip()
            m = _re.search(r'[。；;！!？?\n]', t)
            _eff = _cap if _cap else _LINE_CAP
            if m and 0 < m.start() < _eff:
                t = t[:m.start()].strip()
            elif len(t) > _eff:
                _use_cap = (_cap or _LINE_CAP); seg = t[:_use_cap]
                best = -1
                for _p in '。；，、：;:！？':
                    _i = seg.rfind(_p)
                    if _i > best:
                        best = _i
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
        #   比事实区 15 条的全部篇幅还多。而资产的取用成本极低（一条 mem.py search 就取到），
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
        #   而资产的取用成本极低（要用时一条 mem.py search 就取到），信息密度远低于经验类事实。
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
            _seen_typ = {}
            for _i, _p in enumerate(_picked):
                if _seen_typ.get(_p[1], 0) < _floor_n:
                    _seen_typ[_p[1]] = _seen_typ.get(_p[1], 0) + 1
                    _floor_idx.append(_i)
        _floor_set = set(_floor_idx)
        # ★pin 条目排在最前（先于类型保底）—— 它们是"必须一直在"的定义类结论。
        _pin_idx = [i for i, p in enumerate(_picked)
                    if 'pin' in (p[2]['tags'] or '').lower()]
        _pin_set = set(_pin_idx)
        _fill_order = [(i, _picked[i]) for i in _pin_idx]
        _fill_order += [(i, _picked[i]) for i in _floor_idx if i not in _pin_set]
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
            if _i in _pin_set or _i in _floor_set:
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
        for _i, (_ts, _typ, _r) in _fill_order:
            _d = (_r['updated_at'] or _r['created_at'] or '')[:10] or '????-??-??'
            _item = _hg_fact_line(_d, _typ, _r['source'], _lead(_r['content']))
            if len(_item) + 1 > _room:
                # ★2026-09-16 七修：**跳过**装不下的长条目，继续尝试后面的短条目。
                #   旧实现是 break —— 一条 160 字的巨型事实就能让其后所有短条目全部作废，
                #   实测 headroom 尚余 624 字符却只装进 15 条（预算白白浪费，
                #   且"谁被跳过"取决于 _fill_order 的偶然顺序，不可控）。
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
                _extra.append('- …另 %d 条资产见网关库（mem.py search 或 tool_audit.py audit）'
                              % _dropped_assets)
                break
            _extra.append(_line)
            _rest_in += 1
            _room_assets -= len(_line) + 1
        _tail.extend(_extra)

        mem_lines.extend(_tail)
        mem_lines.append('')
        mem_lines.append('> 导航版：已列 %d 条 / active 共 %d 条（受官方 4000 字符槽位硬限，'
                        '写多会被整体截断）；其余全文用 mem.py search 取。'
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
        _starved = [t for t, _q in _QUOTA if not _type_kept.get(t)]
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
