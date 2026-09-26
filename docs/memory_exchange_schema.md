# Memory Exchange Schema v1

> 状态：**参考实现已落地**（`memtether_exchange.py` / `mem0_exchange.py`）
> 日期：2026-09-26
> 目标：让记忆在多个系统之间迁移时，**不丢治理语义**。

## 1. 为什么不是“导出 JSON”

常见的记忆导出只搬“最新文本”。但治理型记忆库里，**语义在字段之间**：

- 同一件事被修正过，只有 `supersessions` 能说明谁替代谁；
- “API 可用”在 09-15 失效，但系统 09-15 晚才落库 —— 只有双时间轴能区分
  “现实里什么时候成立”（valid time）与“系统什么时候知道”（recorded time）；
- 迁移后该先展示谁，需要 `q_value`；接手系统是否信任这条记忆，需要 `source`。

所以本协议把 **content + source + 双时间轴 + supersession + Q-Value** 当作
不可拆的最小治理单元。

## 2. 文件结构

```json
{
  "schema_name": "memtether.memory_exchange",
  "schema_version": 1,
  "exported_at": "2026-09-26T08:00:00",
  "producer": "memtether | mem0_exchange | <your-system>",
  "producer_db": "/absolute/path/or-urn",
  "counts": {"facts": 0, "supersessions": 0, "tool_assets": 0},
  "facts": [],
  "supersessions": [],
  "tool_assets": [],
  "pii_redacted": 0,
  "sha256": "<digest>"
}
```

### 2.1 `facts[]`

| 字段 | 必填 | 语义 | 缺失时的规则 |
|---|---|---|---|
| `uid` | ✅ | 稳定唯一 ID | 导入端不得猜；没有就跳过 |
| `content` | ✅ | 记忆正文 | 无正文跳过 |
| `type` |  | fact / decision / incident / experience / preference / environment / tool / path | 默认 `fact` |
| `subject` |  | 主语，如 `user` / `system` / `tool:<name>` | 默认 `user` |
| `status` |  | active / superseded / retired / quarantined | 默认 `active` |
| `source` |  | 记忆归属系统或 agent | 默认 `unknown` |
| `valid_from` / `valid_to` |  | 有效时间轴（T） | 缺失用 `recorded_at` 回填 `valid_from` |
| `recorded_at` / `invalidated_at` |  | 摄录时间轴（T′） | **`recorded_at` 缺失不猜**，导入端记为 `needs_temporal` |
| `temporal_source` |  | native / backfilled / inferred | 默认 `backfilled` |
| `superseded_by` |  | 指向替代者的 `uid` | 可为 null |
| `scope` |  | shared / workbuddy / project:<id> 等 | 默认 `shared` |
| `confidence` |  | 0~1 | 默认 0.8 |
| `tags` |  | 字符串或列表；`pin` 表示投影钉住 | 列表转逗号分隔 |
| `q_value` |  | 被采纳价值分 0~1 | 默认 0.5 |
| `use_count` |  | 被采纳次数 | 默认 0 |

> **诚实边界**：不同系统时间格式不一。v1 只归一
> `YYYY-MM-DDTHH:MM:SS`、`YYYY-MM-DD HH:MM:SS`、`YYYY-MM-DDTHH:MM`、
> `YYYY-MM-DD HH:MM`、`YYYY-MM-DD`。其他格式导入端不得猜，只能显式报
> `needs_temporal`。

### 2.2 `supersessions[]`

```json
{"old_uid": "...", "new_uid": "...", "reason": "...", "by_agent": "...", "ts": "..."}
```

替代链必须与 facts 一起迁移。单独搬“最新条目”会丢掉历史可回溯性。

### 2.3 `tool_assets[]`

记录工具资产：`uid` / `name` / `type` / `status` / `path` / `entrypoint` /
`capabilities` / `known_failures` / `last_verified_at` / `source` /
`q_value` / `use_count` 等。资产与事实同池检索是 MemTether 的核心能力之一，
交换协议不把它降级成普通文本。

## 3. 完整性

`sha256` 只对三个记录数组计算，序列化参数固定：

```python
payload = {"facts": ..., "supersessions": ..., "tool_assets": ...}
blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
sha256 = hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

导入端校验失败要显式报 `integrity_ok=false`，不得静默当作成功。

## 4. PII 安全

导出默认走 `memtether_pipeline.sanitize_snapshot()`：手机号、邮箱、API key、
Bearer token、AWS key、Slack token 等会被替换；替换次数写入 `pii_redacted`。
如需导出原始数据，必须显式传 `--no-pii-redact`。

## 5. 导入行为

- UID 已存在 → `skipped`，不覆盖目标端已有数据；
- `recorded_at` 缺失 → `needs_temporal`，不猜时间轴；
- 无效数值 → 回落默认值（`q_value=0.5`、`use_count=0`、`confidence=0.8`）；
- supersession 重复 → `skipped`；
- 空目标库会自动建最小 schema；已有旧库会幂等补列。

## 6. Mem0 适配器

`mem0_exchange.py` 支持三种常见 Mem0 导出形态：

1. `{"memories": [...]}`;
2. `[{"memory": "...", ...}]`;
3. JSONL（每行一个对象）。

内容字段按 `memory / content / text / value / data / fact` 顺序识别。

**诚实边界**：Mem0 没有官方通用导出协议，也通常没有双时间轴 / supersession /
Q-Value。适配器不会伪造这些治理语义：

- 有 `created_at` / `timestamp` 时作为 `recorded_at`，`temporal_source=native`；
- 没有时用导入时刻回填，`temporal_source=backfilled`；
- `valid_from` 同步回填；
- `q_value` 一律 0.5，`supersessions` 为空。

## 7. 参考命令

```bash
# MemTether -> Exchange v1
python memtether_exchange.py export --db memory.db --out exchange.json

# Exchange v1 -> 新 MemTether 实例
python memtether_exchange.py import --from exchange.json --db target.db

# 预演，不写库
python memtether_exchange.py import --from exchange.json --db target.db --dry-run

# Mem0 -> Exchange v1 -> MemTether
python mem0_exchange.py mem0-to-mt --from mem0.json --out exchange.json
python memtether_exchange.py import --from exchange.json --db target.db

# MemTether -> Exchange v1 -> 简化 Mem0 JSON
python mem0_exchange.py mt-to-mem0 --from exchange.json --out mem0.json
```

## 8. v2 方向（未做，不画饼）

- 冲突复核结果（`conflict_reviews`）随包交换；
- 事件链 / tool usage 证据；
- 分片与增量同步（`since` / watermark）；
- canonical entity 别名表；
- 跨系统 ID 命名空间（`producer` + `uid` 组合键）。
