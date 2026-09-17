# Changelog

本文件记录 MemTether 的对外变更。格式遵循
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [PEP 440](https://peps.python.org/pep-0440/)（当前处于 alpha）。

> **关于数字的说明**：本项目**不自报分数、不与其它实现比较数值**。
> 原因见 README「关于可验证性」一节 —— 基准口径不一致时，自报数字是负资产。
> 本文件只记录**行为变更**与**可复现的验证命令**，不记录"提升了百分之几"。

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
