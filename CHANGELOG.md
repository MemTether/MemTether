# Changelog

本文件记录 MemTether 的对外变更。格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [PEP 440](https://peps.python.org/pep-0440/)（当前处于 alpha）。

> **关于数字的说明**：本项目**不自报分数、不与其它实现比较数值**。
> 原因见 README「关于可验证性」一节 —— 基准口径不一致时，自报数字是负资产。
> 本文件只记录**行为变更**与**可复现的验证命令**，不记录"提升了百分之几"。

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
