## [0.1.0a26] - 2026-10-05

### Fixed (audit round 7 — promised-vs-actual sweep)
- **WAL actually enabled**: README/SECURITY claimed "SQLite WAL mode" since a1; journal_mode was `delete` (SQLite default) — concurrent readers hit "database is locked" during writes, in the very multi-client scenario the project exists for. init_db now sets `PRAGMA journal_mode=WAL` (persistent) + `busy_timeout=8000`. Verified: reader succeeds during an open write transaction.
- FTS5 MATCH hardening: embedded double-quotes in user queries no longer produce syntax errors that silently kill the BM25 path per query (quotes stripped per token).
- CORS: wildcard origins retained for keyless localhost use; when MEMTETHER_API_KEY is set, allowed origins are restricted (same-origin only). SECURITY.md gained a CORS section.
- install.ps1 `-Home` now persists MEMTETHER_HOME (setx) — was process-only, so later CLI runs silently disagreed about the data directory; misleading ~/.memtether default message corrected.
- Docker: compose default command referenced mem.py (removed in a20) — now runs the API server; .dockerignore keeps dev junk out of the build context.
- Shipped user-facing instructions referenced removed module: `python mem.py search` replaced with `memtether search` in gateway.py hints, mcp_server tool description, INSTALL.md.

### Verified
- journal_mode=wal; read-during-write succeeds
- FTS queries with embedded quotes no longer raise (covered by except + quote strip)
- keyed CORS: cross-origin requests rejected pre-key
- pytest 30/30; check_packaging rc=0

## [0.1.0a25] - 2026-10-05

### Fixed (audit round 6 — installer/Docker coherence + shipped broken instructions)
- docker-compose default command ran python mem.py stats — mem.py was removed in a20 cleanup; the compose file referenced a module that no longer exists. Now runs the API server (uvicorn), which is what Docker users actually want.
- .dockerignore added: dev _*.py junk, _dev/, build/, *.db etc. no longer leak into the image build context on dev machines (COPY *.py is a wildcard).
- install.ps1: -Home flag now persists MEMTETHER_HOME via setx (was process-only env — later CLI runs silently disagreed); misleading ~/.memtether default messaging corrected.
- Shipped broken instructions: gateway.py / mcp_server.py tool descriptions / INSTALL.md told users to run python mem.py search — mem.py does not exist in the package. All replaced with memtether search. (CHANGELOG historical entries left as-is.)

### Verified
- pytest 30/30; no remaining mem.py references in shipped code/usage docs (CHANGELOG history kept)

## [0.1.0a24] - 2026-10-05

### Fixed (audit round 5 — auth/dashboard coherence + doc rot)
- API key + dashboard coherence: / and /dashboard HTML routes are auth-exempt (static UI); all dashboard fetch() calls attach Authorization from a new optional API-key field (localStorage). Previously setting MEMTETHER_API_KEY bricked the dashboard (every request 401, no way to authenticate).
- MCP add_memories hardcodes scope=shared — now documented in the tool description so agents know private scope is not available over MCP.
- examples/rest_api_client.py port unified to 8820 (was 8080; server/README/dashboard all use 8820).
- hidden_run.pyw docstring referenced memory_maintenance.py which is not in this repo — note corrected to gateway process_events only.

### Verified
- /dashboard 200 without key; /stats 401 without/wrong key; 200 with key
- pytest 30/30

## [0.1.0a23] - 2026-10-05

### Fixed (audit round 4 — split-brain DB defaults)
- **Unified default DB resolution**: gateway / memsearch / CLI / api_server / mcp_server now all route through memtether_paths.default_db(). Prior state had THREE divergent defaults (~/.memtether for CLI, package-dir for engine imports, LOCALAPPDATA for the helper) — a pip user could get three different databases depending on which entry point they used. My a22 default-dir change added the third; a23 makes one truth.
- Resolution order everywhere: MEM_DB env > MEM_HUB_DIR/local_paths.json > LOCALAPPDATA (~/.local/share) per-user dir > package dir only for dev checkouts (or MEMTETHER_IN_PKG=1).

### Verified
- helper / gateway / memsearch resolve identically with no env set
- pytest 30/30; check_packaging rc=0

## [0.1.0a22] - 2026-10-05

### Fixed (audit round 3 — residual leaks from a20/a21 fixes)
- /list and /absorb now exclude private/restricted scope (search was fixed in a20, these two endpoints were not — cross-client private read via dashboard or absorb)
- hubguard db_path: implicit follow-gateway sniffing removed (it silently overrode MEM_DB env); explicit set_test_db()/clear_test_db() marker instead — env precedence restored
- vector store path re-resolves per client call: DB re-pointing (init --force, tests) moves mem0_store with it instead of leaving vectors in the import-time directory
- default data dir for pip users is now %LOCALAPPDATA%/memtether (site-packages installs would lose memory on upgrade); dev checkouts keep repo-root behavior; MEMTETHER_IN_PKG=1 forces old behavior
- stale 86KB test-residue memory.db removed from repo root (was silently picked up as fallback DB on this machine)
- absorb _polarity: governance lexicons imported once at module level (was per-call import)
- README refuse_bench entry carries calibration + known-blind-spot framing

### Verified
- /list and /absorb leak tests: False/False
- pip-like default lands in LOCALAPPDATA; dev fallback unchanged
- chroma follows re-pointed DB; env/test-marker precedence correct in all 3 states
- pytest 30/30; check_packaging rc=0

## [0.1.0a21] - 2026-10-05

### Cleanup (audit plan P2 remainder)
- absorb: contradiction detection now uses governance POS/NEG lexicons (was a 6-word English/Chinese stopword list that missed real polarity words)
- export-md: filename uses full uid (30-char prefix collision risk)
- _search_like_legacy: marked DEPRECATED (kept as last-resort fallback only)
- predicates.check_attr_coverage: explicitly marked NOT-WIRED (honesty note; write-side predicates exist, retrieval-side validation is future work)

## [0.1.0a20] - 2026-10-05

### Fixed (security audit round 2 — all items verified by test)
- FTS5 path resolved DB correctly in pip installs (was hardcoded to package dir; silently broke BM25 in site-packages) — now MEM_DB-aware; :memory: DBs skip FTS
- Ghost-vector guard in near-dup dedup: remember() no longer returns a stale uid when the vector store outlives its facts DB; ghosts purged (root cause of 2 flaky tests)
- Dashboard XSS (last instance): memory list escapes content/type; audit view escapes uid/timestamp
- API key auth actually implemented (MEMTETHER_API_KEY; docs previously claimed it existed): Bearer middleware, /health open, else 401
- scope isolation real: private/restricted excluded from retrieval by default; MEM_SCOPE=all bypass; documented as query-layer (not encryption)
- memtether_pipeline shipped: OWASP guard rules import in clean pip install
- CI runs full tests/ dir (was 2 of 7 files); 30 passed incl. new exchange roundtrip test (replaces allow_module_level skip)
- selfcheck C07 regex matches bracketless CHANGELOG headings
- hubguard: file-lock skipped for :memory:; db_path follows re-pointed gateway.DB
- README: pytest number corrected (30/30), graphiti ref removed; SECURITY.md scope matches implementation

### Verified
- pytest 30/30 single-process (was 2 failed)
- check_packaging rc=0 (19 root modules)
- selfcheck 11/11 PASS rc=0

## 0.1.0a19 (2026-10-04)

### Added
- `GET /timeline/{uid}` — supersession chain API (forward evolution: who replaced this fact)
- Dashboard **记忆审计** card — paste a UID, see the full supersession chain with diff-like status
- `POST /absorb` — semantic absorb endpoint (keyword-overlap classify: duplicate/update/contradiction/related/new, dry_run default)
- `memtether export-md` — export active memories as .md files with YAML frontmatter + manifest.json (portable, greppable, git-able)

## 0.1.0a18 (2026-10-04)

### Added
- `memtether search --list`: L0 summary mode (one-line + uid + score, agent reads full separately)
- `memtether setup <client>`: one-command per-client setup (writes MCP config + verifies)
- Hugging Face Spaces demo link in README Quick Start
- "File, not pipeline" narrative in README header

### Fixed
- Benchmark table synced to 2026-10-02 canonical rerun (55.7% judge / 264-474, ms-judge 72/120)
- E-Hybrid → EAF renamed, full-500Q EAF result (75.7%, 253/334) now in README
- make_demo_db.py recipes schema mismatch (7-column gateway SCHEMA vs old 6-column)
- Known Limitations #3 reframed: junction/symlink is a feature, not "two copies to sync"

# Changelog

本文件记录 MemTether 的对外变更。格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [PEP 440](https://peps.python.org/pep-0440/)（当前处于 alpha）。

> **关于数字的说明**：本项目**不自报分数、不与其它实现比较数值**。
> 原因见 README「关于可验证性」一节 —— 基准口径不一致时，自报数字是负资产。
> 本文件只记录**行为变更**与**可复现的验证命令**，不记录"提升了百分之几"。

## [0.1.0a17] - 2026-10-04

### Added
- Web dashboard (`dashboard.html`) served at `/dashboard` with bilingual UI, live stats auto-refresh, and retire action
- `memtether dashboard` CLI command: starts the API server and opens the browser
- `start_dashboard.bat` one-click launcher (Windows)
- Project icon (`assets/memtether.ico` + `assets/memtether-icon.png`)
- `.github/FUNDING.yml` (GitHub Sponsors)
- MCP Registry badge in README (EN + zh-CN)

### Fixed
- `scripts/check_packaging.py` now passes: py-modules registered `api_server` / `stress_board` / `test_multi_client` / `test_p3_4` / `test_p4_2_generic`; NOTICE declares blake3 / fastapi / uvicorn / httpx
- `api_server.py` internal version string aligned to package version
- Removed stray quote character in `dashboard.html` `<style>` block

## [0.1.0a16] - 2026-10-03

### Added
- `mcp-name` in README for MCP Registry PyPI ownership verification
- `server.json` for MCP Server Registry submission (io.github.lanbass869-cell/memtether)
- SVG demo references in README.zh-CN.md

### Fixed
- Removed `topic_index.json` from repo (contained PII, never should have been committed)
- Refreshed publish_guard baseline

## [0.1.0a9] - 2026-09-28

### Added
- **REST API Server** (`api_server.py`)：FastAPI 8 端点（remember / search / stats / correct / retire / list / qvalue / health），`pip install "memtether[server]"` 安装，`uvicorn api_server:app` 启动
- **LLM 自动记忆抽取** (`extract.py`)：OpenAI 兼容端点，fail-closed（无 key 拒跑、`--source` 必填），`--dry-run` 先看后写
- **Dockerfile + docker-compose.yml**：`docker-compose up` 一键启动
- **Exchange adapter pytest**（13 用例）：6 适配器 × 导出+导入 + 跨适配器集成

### Fixed
- README L324 适配器数量从「5 个」修正为「6 个」（加 COGX）
- README 补 LLM 自动抽取 + REST API 段落
- terminal_demo.svg 插入 README 首屏
- CI 安装 `[server]` extras 使 API 测试在 CI 中运行

### Security
- **PII 脱敏层** `sanitize_snapshot()` 导出快照自动脱敏
- **OWASP 运行时防御** `memtether_guard.py` 提示注入/代码执行/敏感信息/社会工程检测

---

## [0.1.0a8] - 2026-09-26

### Added
- **Memory Exchange 冲突检测端到端 demo**：`scripts/demo_exchange_conflict.py`
  合成 Mem0 导出 20 条 → Exchange Schema v1 → 临时 SQLite → 检出 2 组极性冲突 → 自动退役 → 复检 0 冲突。
  全流程零生产库写入；文档 `docs/exchange_conflict_demo.md` 记录结果与诚实边界。

### Fixed
- `test_autosync.py` / `test_triggers.py`：补 `import os`（4604277 引入的 NameError）
---

## [0.1.0a7] - 2026-09-25

### Fixed
- pyproject.toml UTF-8 BOM causing tomllib failure on Python 3.11+
- regression_test.py missing import os
- memtether.py __version__ drifted to a5 (resynced to a7)
- 7 root modules missing from py-modules (concurrent_stress, memtether_export, memtether_guard, memtether_harness, memtether_paths, memtether_pipeline, test_pii_roundtrip)

## [0.1.0a5] - 2026-09-22

### Fixed

- **`gateway.py qvalue` 子命令不接受 `--source`**：原先只有 `--agent`，而
  `AGENTS.md` / `HARD-RULES.md` 全家约定是 `--source` ⇒ 照文档写会被 argparse
  直接打回 exit 2。改为多选项字符串别名，两种写法等价、dest 不变（旧调用方零影响）。
- **`gateway.py record_tool` 没有 `--source`**：分发处只能落默认来源，
  调用方无法指定归属。补参数并透传。
- **`gateway.py retire` 没有 `--source`，且函数体漏掉来源校验**：`retire()` 是
  唯一不调 `_guard_source()` 的写入函数，by_agent 可直穿审计日志。两处都补。
- **`gateway.py` / `memsearch.py` 共 6 处裸 `import governance`**：新增
  `_load_governance()` 改为按显式文件路径加载，杜绝仓库外同名模块遮蔽
  （对应事故档案 #10 的遗留半边）。
- **`_count_active_facts()` 只统计 facts 不统计 tool_assets**：facts 为 0 而资产
  非空时会误报「空库」，把诊断方向带偏。改为两表相加。
- **stdout 未按 UTF-8 重配导致只读视图崩溃**：`gateway.py qvalue`（不带参数）与
  `peer_msg.py read` 在 GBK 控制台下遇 GBK 编不出的字符直接
  `UnicodeEncodeError`。新增 `_fix_stdio()` 在入口重配。
- **`scripts/scan_leaks.py` 自身会崩**：该脚本是发布前泄密闸门，原先在 `report()`
  里崩掉并返回 exit 1，会被误读成「发现泄密」而真相是根本没扫完。同样重配 stdio。

### Changed

- `memory_hub/README.md`：唯一真源由 `sink.json` 更正为 `memory.db`（v4 起）；
  写入入口统一为 `gateway.py`；移除不存在的 `agentctl.py` 引用。

## [0.1.0a4] - 2026-09-21

本版做了五件事：**修掉一个会让所有新装用户拿到坏 MCP 通道的问题**、
**把客户端接入从手工变成一条命令**、**补回写入路径的来源校验**、
**Windows 一键安装套件**、**修掉 embedding 错误路径上的 AttributeError**。

### 新增

- **Q-Value 覆盖 tool_assets（升级 1 结构性补全）** —— 此前 Q-Value 机制只对
  `facts` 生效：`tool_assets` 没有 q_value/use_count 列、检索候选池里资产没有
  q_value 键（加权兜底 0.5、因子恒 0.65）、`bump_qvalue` 也只更新 facts。
  66 条资产占检索结果近半，「哪条真有用」的信号对它们完全失效。
  现在：① SCHEMA + `_ensure_columns` 幂等给资产补两列（老库自动迁移）；
  ② `bump_qvalue` 双表（uid 不在 facts 时落 tool_assets，返回 `table` 字段）；
  ③ 统计改 UNION 子查询合并 facts + assets；④ memsearch 候选池补 q_value 键。
  实测：`qvalue_upshift_test` 名次 6→3 上升 3 位；发布版 demo 库冒烟 100+10 全绿。
- **Windows 一键安装套件**（`install.ps1` / `install.bat` / `INSTALL.md`）——
  四步流水线：探测 Python >= 3.10 → 幂等装包（版本与 `pyproject.toml` 不一致才重装）
  → 生成演示库 → `tether_connect` 自动接入客户端 → `memtether stats` 收尾自检。
  任一步失败即停，退出码指明死因（1=环境 2=装包 3=演示库 4=接入）。
  路径全部相对 `$PSScriptRoot`，无本机绝对路径。核心链路已用隔离 venv
  （Python 3.10.11）实测：装出 `0.1.0a3`、演示库就绪、stats 正常。

### 修复

- **★embedding 错误路径上的 AttributeError** —— `_embed_local` 在
  `available()=False`（模型目录缺失）时拼错误信息，引用了 `embed_local.MODEL_DIR`
  ——该属性不存在（真实常量是 `HUB` / `MODELS`）。于是"模型文件缺失"这种
  最需要清晰报错的场景，抛出的却是难懂的 `AttributeError`。
  **happy path 永远测不到**（模型在位时该行不执行），只有模型目录缺失时才炸。
  改为 `os.path.join(embed_local.HUB, 'models')`，双路实测：缺失时报可读路径、
  模型在位时 encode 正常（dim=1024）。

- **★MCP 通道「从第一天起就是坏的」（严重）** —— 旧版把重库 `import`
  （`numpy` / `chromadb` 的 C 扩展）放在**后台预热线程**里，而本进程主线程正阻塞在
  `for line in sys.stdin`（MCP 的 stdio 循环）。Windows 下这个组合会**永久死锁**：
  `faulthandler` 实测 25s / 50s 两次 dump 栈完全一致、零进展。
  后果是连锁的，且**都不报错**：
  - `add_memories` 被**永久拒绝** → 写入通道等于不存在；
  - `search_memory` 被**永久降级**成纯 SQLite `LIKE`（中文整串匹配，实测 `count:0`）。
  于是 Agent 试一次「MCP 不能用」就永久退回 CLI，MCP 通道形同虚设。

  修法两件：① 重库 `import` 搬回**主线程**预载（消掉死锁的触发组合）；
  ② 预热加**硬超时**（`_WARM_HARD_TIMEOUT`，默认 25s）——万一将来又卡住，
  也只会降级，不再永久拒绝。

  最小复现（与 import 哪个模块无关，只与「主线程阻塞 stdin」有关）：
  ```
  主线程 time.sleep   → import numpy 0.07s / import chromadb 0.69s   ✅
  主线程读 stdin 管道 → 两者均 HANG > 60s                            ❌
  ```

- **写入路径完全没有来源校验** —— 脚本把自己的名字当 `source` 传进来会被照单全收，
  归属被写花且没人会注意到（与「静默覆盖」同族：出错时不报错）。
  现补上 `_known_sources()` / `_guard_source()`，并接到
  `remember` / `correct` / `record_tool` 三个写入入口。

  行为定义（**fail-open，不 fail-closed**）：
  - 读不到 `agents.json` ⇒ **放行**（不能让「配置缺失」变成「写不进记忆」）；
  - 中性默认值 `local` / `unknown` ⇒ **永远放行**（它们是「未识别来源」的诚实标记）；
  - 未注册来源 ⇒ 有 `MEM_SOURCE_FALLBACK`（且该值已注册）则**软着陆回退** + 记 warning，
    否则硬报错；`MEM_SOURCE_GUARD=0` 可应急放行。
  - **★为什么不直接 `raise SystemExit`**：它**不被** `except Exception` 捕获，
    长驻进程（MCP server）会**直接死掉** —— 客户端侧表现为工具静默消失，
    且不会自动拉起。比报错更糟。

### 新增

- **`memtether-connect` —— 客户端自动接入器**。MCP 本身是手动档：找配置文件、
  照 schema 写 JSON、（Electron 系）再去 UI 点一次信任；而每个客户端的 schema
  都不一样，漏一个就有一个客户端读不到记忆。现在是一条命令：

  ```bash
  memtether-connect detect     # 发现本机装了哪些客户端、配置在哪、接没接
  memtether-connect plan       # 预演，不写盘
  memtether-connect apply      # 写入（备份 + manifest，可回滚）
  memtether-connect verify     # 校验配置内容 + 信任状态
  memtether-connect rollback --stamp <时间戳>
  ```

  23 个适配器，分两类：`clients/standard.py`（标准客户端，路径与 schema 逐条
  核对社区维护的 agent config 参考表）与 `clients/local.py`（本机实测的 Electron 系与 dsh 系）。

  写盘的四条硬约束：
  - **不猜** —— 找不到约定的根路径（schema 变了）就**失败关闭**，
    绝不「大概写在这儿」。写坏用户的配置比不写更糟。
  - **保注释** —— 配置带 `//` 注释或尾逗号时（VS Code 系常见），用 JSONC 感知的
    编辑器**只改该改的那一处**，不整份重排。
  - **幂等** —— 语义一致就**一个字节都不写**（路径分隔符风格、重复斜杠、空 `env`
    先归一化再比较）。否则「每次跑都改写一遍本来正确的配置」。
  - **只碰装了的** —— 配置落在主目录/共享目录的客户端（`~/.claude.json` 的父目录
    必然存在）会被误判成「已安装」，故额外查安装痕迹，找不到就跳过。

  另含两项此前只能手工做的事：**来源名注册**（不注册 ⇒ 写入被兜底成别的名字、
  归属串号）与 **Electron 系信任代写**（按客户端同一算法
  `sha256(command|sorted(args)|sorted(env keys))` 算键，省掉「UI 显示已连接、
  Agent 拿不到工具」那一步）。

- **MCP server 的来源自动识别（零配置）**。原先只认 WorkBuddy 两版
  （硬编码两个子串），换任何别的客户端都识别不出 ⇒ 不传 `source` 的写入
  被兜底成 `workbuddy`、归属串号。现改为**表驱动 + 两级匹配**：

  1. **父进程映像全路径** —— 覆盖 Electron 系（`WorkBuddy.exe` / `ZCode.exe` / Tabbit…）；
  2. **父进程命令行**（读 PEB）—— 覆盖「被通用宿主拉起」的情况：
     独立 dsh / Claude Code 的父进程就是普通 `node.exe`，
     只有命令行里才带 `@deepseek-ai/dsh` / `claude-code`。

  匹配按签名长度降序（保证 `workbuddyai` 先于 `workbuddy` 命中）；
  兜底值**中性化**为 `local`（可用 `MEM_FALLBACK_SOURCE` 覆盖）；
  用户可在同目录放 `client_signatures.json` 扩展，不必改代码。

  > **为什么不直接用 `env` 配来源**：`env` 的 key 集合参与信任 hash，
  > 多一个 key 就掉信任，而掉信任后 server 会被客户端**静默跳过**
  > （只有日志里一行 `skipping untrusted`）。所以能自动就别让用户配。

### 变更

- **行尾归一 LF + `.gitattributes` 入版本库** —— 本仓是开源发布库，跨平台 clone
  是主场景。此前只靠局部 `core.autocrlf=false` 抵消 system 级 `true`
  （未入库，换机即失效），且 4 个历史文件（`tool_audit.py` /
  `asset_bench_holdout.py` / `rerank_k_bench.py` / `.release-baseline.json`）是 CRLF。
  本次 `*.py/*.md/*.json/*.toml/*.cfg/*.txt` 全部 `text eol=lf` 并 renormalize
  （内容不变仅行尾）。根治跨仓 diff 假象：09-20 审计实测 memory_hub↔memtether 的
  `memsearch.py` 裸 diff 报 1849 行噪音，剥掉 CR 后真实差异仅 110 行——警戒线虚高 15 倍。
- `pyproject`：`packages` 增加 `clients`（否则 `pip install` 出来的包**不带**
  适配器包，工具一跑就 `ImportError`）；`py-modules` 增加 `tether_connect`；
  新增 console script `memtether-connect`。
- `.gitignore`：开发产物归口到已忽略的 `dev/`；忽略 `claw_channel_watchdog.py`
  （被开机自启按**绝对路径**引用，故就地保留而非移走）。

### 可复现的验证

```bash
# ① 客户端接入器自检（16 项：语义等价不写盘 / 安装判据 / Codex 原生 TOML 写法识别 / 同名表失败关闭）
python tether_connect.py selftest

# ② 本机探测与预演（只读，不写盘）
python tether_connect.py detect --hub-dir <你的中枢目录>
python tether_connect.py plan   --hub-dir <你的中枢目录>

# ③ JSONC 编辑器自检（24 项：注释 / 尾逗号 / CRLF / 缩进风格 / 深层路径）
python clients/jsonc.py

# ④ 打包清单闸门（模块清单与 pyproject 是否一致）
python scripts/check_packaging.py
```

本机实测结论（隔离库 stdio 探针，非推断）：`initialize 0.27s` →
`tools/list 0.64s`（3 个工具）→ `add_memories 0.95s`（`ok:true`）→
`search_memory 0.01s`（`engine=hybrid`，未降级）。
来源识别对**真实进程**实测：`WorkBuddy.exe→workbuddy`、
`WorkBuddyAI.exe→workbuddy_ai`、`ZCode.exe→zcode`、`Tabbit Browser.exe→tabbit`、
`node.exe`（OpenClaw 网关）`→openclaw`。

---

## [0.1.0a3] — 2026-09-18

新增两项记忆治理能力（**默认中性，不改变任何现有排序**），并修掉一处检索误伤。

### 新增

- **投影钉住 `pin`** —— 把「必须一直在」的定义类结论排除在时间竞争之外。
  不新增 schema，复用 `tags` 字段（含 `pin` 即视为钉住）；`rebuild()` 在选条阶段把
  pin 条目**补进**已选集合，因此不会被配额或时间序挤出。软上限
  `MEM_PROJ_PIN_MAX`（默认 10），超限时重建会打印告警并给出释放命令。
  **★改完必须 `rebuild` 才生效** —— 它只影响投影，不影响检索。
  ```bash
  python gateway.py pin <uid>        # 钉住
  python gateway.py pin <uid> --off  # 释放
  python gateway.py rebuild
  ```

- **被采纳价值分 Q-Value** —— 检索命中**被采纳**后回写，下次排序上浮。
  - `facts` 新增 `q_value REAL DEFAULT 0.5` 与 `use_count INTEGER DEFAULT 0`；
  - 回写公式 `q += LR * (reward - q)`（`LR = 0.1`，约 10 次观测收敛到长期均值）；
  - 检索侧**只读**，因子 `score *= (0.3 + 0.7 * q)`，挂在时间衰减**之后**
    （塞进衰减块内会被自带重排覆盖；且衰减可被调用方关掉，挂在那里会漏算）；
  - 开关 `MEM_QVALUE=0` 关闭；回写失败只告警一次并退化为纯相关性排序，**不崩**。
  ```bash
  python mem.py qvalue                     # 只读：看分布 / Top 榜
  python mem.py qvalue <uid> --reward 1    # 1=完全采纳 / 0.5=部分有用 / 0=检索到但没用
  python mem.py qvalue <uid> --dry-run     # 只算不写
  ```
  > **它现在不等于"效果提升"**：全库 `q_value` 默认 0.5 → 因子恒为
  > `0.3+0.7×0.5 = 0.65`，对同一批候选是**同一常数**，在 RRF → min-max 精排链路里
  > 被完全抵消。而且"被采纳"目前**只能由调用方显式回写**，本项目没有自动判定机制。
  > 所以本版能证明的是**「机制正确且零副作用」**（见下方验收台），不是效果。

- **旧库自动迁移**：`gateway.init_db()` 现在会调 `_ensure_columns()` 幂等补列。
  `CREATE TABLE IF NOT EXISTS` 对**已存在**的旧库不生效 —— 旧库缺 `q_value` 时，
  `memsearch` 的显式 `SELECT q_value` 会直接抛异常，被外层 `except` 兜住后
  **检索整体降级**（不报错、但结果错）。这是本次特意堵的坑。
  另附独立迁移脚本 `migrate_qvalue.py`（**默认只读检测**，`--apply` 才落库）。

- **验收台（源码在仓库里，可自行复跑）**：
  - `qvalue_ab.py` —— 开/关 A/B 对照。判据三条：分数**不得下降**、
    逐题 PASS/FAIL 不翻转、逐题 Top10 内容序列**逐字节一致**。
    做法是设 `MEM_QVALUE=0/1` 各跑一遍（子进程继承 env），
    因此两侧跑的是同一份代码、同一个索引，不改验收台、不重建索引。
  - `qvalue_upshift_test.py` —— 排序上移实测。用**副本库 + 正本索引**
    做单变量对照（轮 A 全库 0.5 → 轮 B 只把中位条目调到 0.99），
    避免在生产库上写真值。找不到可用库时**显式报错**，不拿空库硬跑。

### 修复

- **自指误伤（检索）**：`memsearch._is_self_referential` 情形 1 原实现写的是
  `if q in c:`，漏掉了同段注释里那半句「**含空格**」。后果是**单词查询**
  （单个专有名词 —— 工具名 / 模块文件名 / 内部代号这类）只要命中的记忆里出现
  任意一个 `_SELFREF_TELL` 词（「实测 / 结论：/ 之前 / 必须 / 缺 / 坑」——
  而这类词在真实记忆库里几乎条条都有），就被误判成"在谈论这个查询"，
  `score ×0.05` 打入冷宫。现改为 `if q in c and ' ' in q.strip():`。
  单词查询的真自指（如"实测：搜 XXX 返回 0 条"）仍由情形 2 兜住。

### 变更

- 版本 `0.1.0a2` → `0.1.0a3`（`pyproject.toml` 与 `memtether.py __version__` 同步）。
- `py-modules` 补 3 项：`migrate_qvalue` / `qvalue_ab` / `qvalue_upshift_test`
  （`scripts/check_packaging.py` 会校验清单与仓库实际文件一致）。
- README：补 `pin` / `qvalue` 用法与「它现在是什么水平」里的两条诚实说明。

### 验证方式（可复现）

```bash
python scripts/check_packaging.py      # 打包清单 vs 仓库实际文件
python scripts/scan_leaks.py           # 发布前泄密扫描（需 MEM_SCAN_TERMS 指向词表）
python scripts/make_demo_db.py         # 先要有一份可检索的库
export MEM_DB=demo/memory_demo.db
python qvalue_ab.py                    # 判据：分数不下降 + PASS/FAIL 不翻转 + Top10 一致
python qvalue_upshift_test.py          # 判据：被提升条目名次上升
```

> `qvalue_upshift_test.py` 若未指定 `--query`，会从库里取一段**真实存在**的连续串
> 当查询词 —— 硬编码一个查询只在那台机器上成立，换一份库就返回 0 条，
> 脚本会看起来"跑通了"其实什么也没测。

---

## [0.1.0a2] — 2026-09-17

修复重发，无新功能。`0.1.0a1` 的发布包**不含**下列修复。

### 修复

- **发布泄露面**：`.gitignore` 原先只写 `*.bak` 与 `*.bak_*`（下划线），
  **匹配不到** `<file>.bak-<YYYYMMDD-HHMMSS>`（连字符）这种快照名 ——
  审计工具自己产生的快照会漏进发布集。现合并为 `*.bak*`。
- **默认归属中性化**：`gateway.py remember` 的 `--source` 缺省值由
  `workbuddy` 改为中性值 `local`；开源仓库不预设任何客户端名。
- **打包闸门**：`py-modules` 补齐至 52 项（`publish_pypi` / `wslog_append` /
  `slot_update` 此前漏列，`scripts/check_packaging.py` 会报红）。
- **冒烟脚本**：`smoke_bitemporal.py` 用了 `os` 却没 `import os`，
  直接运行会 `NameError`。

### 变更（脱敏）

- 清除 6 处硬编码本机路径，改为环境变量 + 相对推导：
  `attach_hubguard.py`（`--target` 缺省取 `$MEM_HUBGUARD_TARGET`，
  未设则推导为同级 `memory_hub/gateway.py`；注入块查找 `hubguard.py`
  走 `$MEM_HUBGUARD_PATH`）、`hubguard.py` 文档、
  `patch-memory_hub-hubguard.diff`（按 `7bb566b` 基线重新生成）、
  `.release-baseline.json`、`scripts/scan_leaks.py` 注释。
- `ARCHITECTURE.md` 重写为开源架构文档并脱敏；README 新增「仓库里的文件地图」。

### 验证方式（可复现）

```bash
python scripts/scan_leaks.py            # 发布集脱敏扫描（缺词表 exit 2）
python scripts/scan_history_leaks.py    # git 历史扫描
python scripts/check_packaging.py       # 打包清单与版本号闸门
python hubguard.py selftest             # 7 组并发 / 原子性自检
```

---

## [0.1.0a1] — 2026-09-17

首个 alpha。研究原型，接口未冻结。

### 新增

**核心**
- `gateway.py`：记忆条目的唯一写入入口，每条记忆都带 `--source <来源名>`
  归属标记（默认 `workbuddy`）；支持
  `remember / correct / retire / record_tool / incident / search / rebuild` 等子命令。
- 双时间轴（bi-temporal）治理：有效时间 `T` 与摄录时间 `T′` 分离。
  命令行入口在 `mem.py`：`asof`（某时刻什么为真 / 系统当时认为什么为真）
  与 `timeline`（沿替代链还原一条事实的演化过程），
  由 `gateway.py` 的 `as_of()` / `timeline()` 实现。
- 事实与**工具资产同池检索**，每条资产可带可执行校验命令。
- 混合检索：向量 + 关键词 + 字面，RRF 融合后精排。
- 本地 embedding 兜底（`bge-m3 int8`，1024 维），断网可跑；
  语义路不可用时**自动降级为关键词 + 字面**并打印 `[warn]`，不崩溃。

**跨客户端共享**
- 文件级指针方案：多个客户端指向**同一份物理 `memory.db`**，不做同步。
- `slot_update.py`：共享注入槽位的原地更新器。把六步安全配方固化成一条命令 ——
  锚点唯一化 / 预算闸门（只读试算）/ 同目录 `.bak-<时间戳>` 快照 /
  同目录 `mkstemp` + `os.replace` 原子写 / 换行跟随目标 / 回读逐字节比对。
  **任一步失败即整体中止，一个字节都不写。** 支持 `--dry-run` 与 `--selftest`。
- `wslog_append.py`：多写者共写同一日志的原子追加器。用
  `CreateFileW(FILE_APPEND_DATA)` + `WriteFile` 实现真正的原子追加
  （Windows CRT 的 `O_APPEND` 是 seek + write，非原子，多进程会互覆）。
  并发演练 8 进程 × 50 行：原子追加丢行 0；对照的读-改-写大量丢行
  （数百条，随竞态波动，不固定）。

**并发与一致性**
- `hubguard.py`：跨进程写锁（`hub_lock`）+ 写函数包装（`install_guards`）；
  同一 inode 整体重写的防护（`commit_guarded` / `snapshot` / `same_snapshot`）。
- `attach_hubguard.py`：把上述机制接进既有 `gateway.py` 的接入器 ——
  投影条目保留**来源**标记（`_hg_fact_line` / `_count_tagged`，实现在 `gateway.py`）；
  支持 `check` / `patch` / `apply` / `revert`。

**脱敏与发布前检查**
- `scripts/scan_leaks.py`：对**发布集**（`git ls-files` 口径）扫描敏感串，
  分 BLOCK / WARN 两档，**词表缺失时失败闭锁**（exit 2 + "无法判定"），绝不输出假绿。
- `scripts/scan_history_leaks.py`：对 **git 历史文本 blob** 扫描。
  历史里的命中无法靠改文件消除，必须重写历史 —— 工具会明确说明这一点。
- `scripts/make_demo_db.py`：生成**全合成**演示库（不含任何真实数据），
  自带三项自检：泄密检查 / 表结构一致性 / 功能冒烟。
- `publish_pypi.py`：PyPI 发布器。显式上传名单（只认 `whl` + `tar.gz`，
  **绝不用 `dist/*`**）；token 只从环境变量或剪贴板读，**绝不落盘、不进 git**；
  三步闸门（归档杂物 → `twine check` → 上传），任一不过即中止。

### 已知限制

- **不包含真实记忆数据。** 仓库只提供**合成演示库**；真实库含个人标识信息，
  不在开源范围内。这是刻意设计，不是遗漏。
- **未发布到 PyPI。** 目前请从 git 安装：
  `pip install "memtether @ git+https://github.com/MemTether/MemTether"`。
- 一键安装脚本尚未提供。
- 语义检索需额外装 `chromadb / onnxruntime / tokenizers`；
  缺省安装下检索会降级为关键词 + 字面（会打印 `[warn]`，属**已知降级**，
  不视为通过 —— `make_demo_db.py --strict` 下会判失败）。
- 本机 git 身份已治理为中性值；若你 fork 后提交，请自行确认
  `git config user.name/user.email` 不含个人标识。

### 验证方式（可复现）

```bash
# 1) 生成演示库并跑三项自检（无本地词表时诚实报「无法判定」）
python scripts/make_demo_db.py

# 2) 发布集脱敏扫描（需自备词表；缺词表会 exit 2 而不是假绿）
python scripts/scan_leaks.py

# 3) git 历史脱敏扫描
python scripts/scan_history_leaks.py

# 4) 共享槽位更新器自检（临时目录，不碰线上）
python slot_update.py --selftest

# 5) 共写日志原子追加演练
python wslog_append.py --selftest
```

### 许可

Apache-2.0（含专利授权）。第三方归属见 `NOTICE`。

---

## 版本路线图（非承诺）

- `0.1.0a2`：修复重发（无新功能），见上。
- 后续 alpha：一键安装脚本；把自检收敛成单条命令并输出报告。
- `0.2.0`：MCP Server 封装（`add_memories` / `search_memory` /
  `list_memories` / `delete_all_memories`）；技能自动沉淀闭环。
- 接口在 `0.1.x` 期间可能变动，`1.0` 前不做兼容承诺。

## [0.1.0a10] - 2026-10-01

### Added
- Bilingual query expansion: `synonyms.json` with 47 zh-en synonym groups
- `memsearch.py` auto-loads synonyms.json and expands search terms before FTS/vector lookup
- Tool assets added to FTS5 index (previously only in vector store)

### Fixed
- FTS5 index: cleaned 420 stale entries, rebuilt 194 missing entries (100% active coverage)
- Q-Value differentiation: procedures=0.8, decisions=0.6, incidents=0.55, high-use=0.65 (was 99.7% flat at 0.5)
- Tool assets Q-Value: bumped to 0.7 (was 0.5)
- Vector index consistency: 1215==1215 (removed 1 stale entry)
- Removed machine-specific proxy port from synonyms.json

### Improved
- asset_bench: 22/23 → 23/23 (A1 "eNSP 装在哪里" now finds tool_asset with correct path)
- Cross-language search: browser↔浏览器 overlap from 0 to 10/10
- hard_bench: 62/62 (no regression)
- e2e_verify: 13/13 (no regression)
## [0.1.0a11] - 2026-10-01

### Added
- Four-factor re-ranking (MemX arXiv 2603.16171): semantic(0.45) + recency(0.25) + frequency(0.05) + importance(0.10) blended with RRF (70/30)
- Z-score + sigmoid normalization for cross-query score comparability
- Three-layer deduplication: supersession-aware + content + tag-signature (MemX Section 3.5)
- Low-confidence rejection marker in diag (MemX Section 3.6, threshold 0.50)
- Self-referential poisoning fix v2: narrower criteria (only meta-discussion triggers)
- Procedure quality check: first sentence must be complete conclusion (>=15 chars)
- Q-Value write-back on retrieval (bump inside search_hybrid, MEM_BUMP env var to disable)

### Fixed
- Tool assets added to FTS5 index on record_tool (previously missing)
- Vector index ghost entries cleaned on supersede (previously accumulated)
- EMBED_BACKEND_DEFAULT and LAST_EMBED_INFO initialization (pre-existing bugs)
- mcp_server.py duplicate bump removed (now handled in search_hybrid)

### Performance
- hard_bench: 62/62 (no regression)
- asset_bench: 23/23 (no regression)  
- e2e_verify: 13/13 (no regression)
- Cross-language search: browser/browser 10/10, STM32 UART/serial 8/10
## [0.1.0a12] - 2026-10-01

### Added
- Question type detection: counting/comparison/temporal/knowledge-update/aggregation
- Deterministic reasoning scaffolds (Auditable Memory arXiv 2609.38021 Stage 4)
  - counting: entity occurrence counting in results
  - temporal: chronological ordering of results
  - comparison: top result pairing
  - aggregation: type/source distribution summary
- Coverage-first packet compiler (Auditable Memory Stage 3): max 16 items, 12k chars
  - Phase 1: top-scored results
  - Phase 2: diversity bonus for unseen types/sources
  - Character budget trimming
- All scaffolds and packet metadata exposed in diag for full auditability

### Improved
- Question type detection priority reordered (counting before aggregation)
- Scaffold integrated into search_hybrid() return pipeline