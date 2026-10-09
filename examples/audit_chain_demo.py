"""examples/audit_chain_demo.py — 演示记忆法庭：写 20 条 → 篡改 1 条 → verify_chain 变红

这是 MemTether 最核心的创新：任何直接改库的静默篡改都会被哈希链抓住。
"""
import sys, os, sqlite3, tempfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# isolated scratch db so the demo never touches real data
_db = os.path.join(tempfile.gettempdir(), 'memtether_audit_demo.db')
if os.path.exists(_db):
    os.remove(_db)
os.environ['MEM_DB'] = _db
os.environ['MEM_SKIP_VECTOR'] = '1'
from gateway import remember, init_db, get_conn
import memory_court as mc

init_db()

# 1. 写 25 条（超过 BATCH=10 自动触发两次锚点）
print("写入 25 条记忆（自动生成 2+ 个审计锚点）...")
for i in range(25):
    remember(f"审计演示条目 {i}: 系统配置项 {chr(65+i%6)}={i*3}", source="demo")

# 2. 验证链：当前应该 PASS
conn = get_conn()
ok, detail = mc.verify_chain(conn)
print("篡改前 verify_chain:", "PASS" if ok else "FAIL", detail)

# 3. 模拟攻击者：直接改库（绕过应用层）
print("\n模拟攻击者直接改库...")
conn.execute("UPDATE audit_log SET detail='TAMPERED' WHERE id=(SELECT MIN(id) FROM audit_log)")
conn.commit()

# 4. 再次验证：必须 FAIL
ok2, detail2 = mc.verify_chain(conn)
print("篡改后 verify_chain:", "PASS" if ok2 else "FAIL", detail2)
if not ok2:
    print("\n✅ 哈希链成功检测到篡改！这就是 MemTether 的核心防御能力。")
conn.close()