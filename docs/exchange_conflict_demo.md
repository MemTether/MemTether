# Memory Exchange 冲突检测端到端 demo（M2）

**脚本**：`scripts/demo_exchange_conflict.py`
**日期**：2026-09-26
**状态**：✅ 全流程 PASS

## 它证明什么

MemTether 的 Memory Exchange Schema v1 不只是"能搬数据"，而是**治理语义随记忆一起迁移**。
这个 demo 用全合成数据在临时库里走完整链路：

1. 构造 Mem0 风格导出（20 条记录）
   - 14 条正常记忆
   - 2 条重复 UID（m-003 / m-007 各重复一次）→ 测导入去重
   - 2 组极性冲突：
     - `GPTX_ASTRA_KEY 可用` (60 分钟前) vs `GPTX_ASTRA_KEY 失效（403）` (30 分钟前)
     - `SILICONFLOW 余额不足，已失效` (60 分钟前) vs `SILICONFLOW 已充值，可用` (30 分钟前)
2. `mem0_to_exchange` → Memory Exchange Schema v1（带 sha256 完整性）
3. `import_exchange` → 临时 SQLite（`E:\Temp\mt_demo_conflict_*`）
   - 实测：facts_inserted=18, facts_skipped=2（重复 UID 正确去重）, integrity_ok=true
4. `governance.detect_explicit_conflicts(90)` → **检出 2 组冲突**（实体归一：gptx_astra / siliconflow）
5. 对每组自动退役旧条 → `retire(suggest_retire, by_uid=keep, apply=True, force=True)`
6. 复检 `detect_explicit_conflicts` → **0 冲突** ✓
7. 断言旧条 `status='superseded'`、`superseded_by=keep`、`valid_to` 非空；新条保持 `active`

## 诚实边界

- demo 数据是合成的，实体名沿用 `_STATUS_ASSERT` 正则表里的已知实体。
  换一个不在正则表里的实体（如 `FOO_SERVICE`）检测不到——这是当前精确检测的已知边界，
  也是"精确优先于召回"设计的直接后果。
- `force=True` 在 demo 里是安全的（合成条只有单一句子，无残留事实）。
  生产场景中，含有独立事实的混合条目会被 `_residual_facts` 拦下要求人工确认，
  demo 脚本绕过它不代表生产也该绕过。
- demo 用的实体归一依赖 `_ALIAS` 表和 `_canonical()` 的后缀剥离启发式，
  实体名大写/带 `_key` 后缀能归一，但完全不同的别名（如 `my_llm` vs `openai_api`）不会自动合并。

## 复现

```bash
cd E:\RUANJIAN\memtether
E:\RUANJIAN\memory_hub\.venv-memory\Scripts\python.exe scripts\demo_exchange_conflict.py
# 加 --keep 可保留临时目录调试
```

退出码 0 = PASS；脚本本身零生产库写入（governance.DB 在脚本内显式指向临时库）。
