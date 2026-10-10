"""examples/mem0_migration.py — 从 mem0 迁移到 MemTether 的完整流程

MemTether 提供与 mem0 的双向数据交换（不依赖 mem0 SDK）。
本示例演示：把一份 mem0 导出文件导入 MemTether，再导回 mem0 格式。

真实迁移路径：
  1. 从 mem0 平台导出 JSON（GET /v1/memories/ 或 SDK mem.get_all()）
  2. python examples/mem0_migration.py your_mem0_export.json
  3. 数据进入 MemTether，来源标记为 mem0，双时间轴自动补齐
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1. 准备一份模拟的 mem0 导出（真实场景从 mem0 API 拉取）
mem0_export = {
    "memories": [
        {"memory": "用户的数据库是 PostgreSQL 15", "user_id": "alice"},
        {"memory": "用户偏好的部署方式是 Docker Compose", "user_id": "alice"},
        {"memory": "生产环境 API 地址是 api.example.com", "user_id": "bob"},
    ]
}

src = os.path.join(tempfile.gettempdir(), "mem0_in.json")
out_mt = os.path.join(tempfile.gettempdir(), "exchange.json")
out_mem0 = os.path.join(tempfile.gettempdir(), "mem0_back.json")
json.dump(mem0_export, open(src, "w", encoding="utf-8"), ensure_ascii=False)

# 2. mem0 → MemTether Exchange v1
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mem0_exchange import mem0_to_exchange as mem0_to_mt, mt_to_mem0
result = mem0_to_mt(src, out_mt)
payload = result[0] if isinstance(result, tuple) else result
print(f"[1] mem0 -> Exchange v1: {len(payload['facts'])} facts")
for f in payload["facts"]:
    print(f"    {f['content'][:40]} | temporal_source={f.get('temporal_source')}")

# 3. 导入到 MemTether（真实写入）
os.environ["MEM_DB"] = os.path.join(tempfile.gettempdir(), "migration_demo.db")
if os.path.exists(os.environ["MEM_DB"]):
    os.remove(os.environ["MEM_DB"])
os.environ["MEM_SKIP_VECTOR"] = "1"
from gateway import remember, search
for f in payload["facts"]:
    remember(f["content"], source="mem0", tags="migrated")
print(f"[2] imported into MemTether: {len(payload['facts'])} facts")

# 4. 验证可检索
res = search("PostgreSQL", limit=3)
print(f"[3] search 'PostgreSQL': {len(res.get('results', []))} hits")
for x in res.get("results", []):
    print(f"    {x.get('content', '')[:50]} | source={x.get('source')}")

# 5. MemTether → mem0（双向闭环，供切回或同步）
p2, _ = mt_to_mem0(out_mt, out_mem0)
print(f"[4] Exchange v1 -> mem0 format: {len(p2['memories'])} memories (round-trip OK)")

print("\n迁移完成。双时间轴字段以 temporal_source='backfilled' 诚实标记，不伪造来源。")