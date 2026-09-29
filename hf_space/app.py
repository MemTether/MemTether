# -*- coding: utf-8 -*-
"""MemTether HF Space demo — Gradio UI on top of demo DB (self-contained, no secrets)."""
import os, sys, json, sqlite3, hashlib, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_demo.db")

def q(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

# ---------- keyword search (no heavy deps; mirrors production keyword path) ----------
def _tokens(qstr):
    import re
    return [t for t in re.split(r"[\s,，。;；]+", (qstr or "").strip()) if t]

def keyword_search(query, limit=8):
    toks = _tokens(query)
    if not toks:
        return []
    rows = q("SELECT uid, type, source, content, updated_at FROM facts WHERE status='active'")
    scored = []
    for r in rows:
        text = (r["content"] or "").lower()
        s = 0
        hits = 0
        for t in toks:
            tl = t.lower()
            if tl in text:
                hits += 1
                s += text.count(tl) * (2 if len(tl) > 3 else 1)
        if hits:
            r["score"] = s / (1 + 0.1 * hits)  # simple tf normalization
            r["hits"] = hits
            scored.append(r)
    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]

def do_search(query, limit):
    t0 = time.time()
    rows = keyword_search(query, int(limit))
    ms = round((time.time() - t0) * 1000)
    if not rows:
        return f"**❌ 未命中**（查询「{query}」在 {stats_total()} 条 active 记忆里没有找到 —— 这是拒答能力：库里没有就说没有）", ""
    lines = []
    detail = []
    for i, r in enumerate(rows, 1):
        head = (r["content"] or "").split("。")[0][:140]
        lines.append(f"**{i}.** `{r['uid'][:22]}…` [{r['type']}·{r['source']}] {head}")
        detail.append(f"**uid**: {r['uid']}\\n\\n**updated**: {r['updated_at']}\\n\\n**content**: {r['content'][:600]}\\n\\n---")
    return "\\n\\n".join(lines), "\\n\\n".join(detail) + f"\\n\\n*检索耗时 {ms}ms · 关键词路（向量/FTS5/PPR 需本地部署体验）*"

def stats_total():
    return q("SELECT COUNT(*) c FROM facts WHERE status='active'")[0]["c"]

def do_remember(content, mtype, source, tags):
    if not content.strip():
        return "❌ 内容为空"
    uid = f"fact-{time.strftime('%Y%m%d%H%M%S')}-{hashlib.md5((content+str(time.time())).encode()).hexdigest()[:12]}"
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO facts (uid, type, content, source, recorded_at, updated_at, status, tags, valid_from) VALUES (?,?,?,?,?,?, 'active', ?, ?)",
            (uid, mtype, content.strip(), source.strip() or "visitor", time.strftime("%Y-%m-%d %H:%M:%S"), time.strftime("%Y-%m-%d %H:%M:%S"), tags.strip(), time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return f"✅ 已写入（演示库）：`{uid}`\\n\\n来源={source} 类型={mtype} —— 这是 gateway.remember() 的最小行为（归属强制+双时间轴）"
    except Exception as e:
        return f"❌ {e}"
    finally:
        conn.close()

def do_stats():
    total = q("SELECT COUNT(*) c FROM facts")[0]["c"]
    active = stats_total()
    superseded = q("SELECT COUNT(*) c FROM facts WHERE status='superseded'")[0]["c"]
    assets = q("SELECT COUNT(*) c FROM tool_assets")[0]["c"]
    by_type = q("SELECT type, COUNT(*) c FROM facts WHERE status='active' GROUP BY type ORDER BY c DESC")
    by_source = q("SELECT source, COUNT(*) c FROM facts WHERE status='active' GROUP BY source ORDER BY c DESC LIMIT 8")
    t = "\\n".join(f"- **{r['type']}**: {r['c']}" for r in by_type)
    s = "\\n".join(f"- **{r['source']}**: {r['c']}" for r in by_source)
    return f"""### 演示库统计

| 指标 | 值 |
|---|---|
| facts 总数 | {total} |
| ├ active | {active} |
| ├ superseded | {superseded} |
| tool_assets | {assets} |

**按类型（active）**\\n{t}

**按来源（active, top8）**\\n{s}

> ⚠ 这是全合成演示库（make_demo_db.py 生成）。生产实例含真实个人数据，永不公开。
"""

def do_timeline(uid):
    rows = q("""SELECT old_uid, new_uid, reason, ts FROM supersessions WHERE old_uid=? OR new_uid=? ORDER BY ts""", (uid, uid))
    if not rows:
        return f"`{uid}` 无替代链记录（或 uid 不存在）。试试 demo 库里 superseded 的 uid（先搜索后复制 uid）。"
    return "\\n".join(f"- `{r['old_uid'][:22]}…` → `{r['new_uid'][:22]}…` ({r['reason']}) @ {r['ts']}" for r in rows)

def do_conflicts():
    rows = q("""
        SELECT a.uid uid_a, b.uid uid_b, a.content ca, b.content cb, a.source sa, b.source sb
        FROM facts a JOIN facts b ON a.uid < b.uid
        WHERE a.status='active' AND b.status='active'
        LIMIT 400
    """)
    import re
    pos = ("可用", "成功", "通过", "正常", "支持", "有效", "可以")
    neg = ("不可用", "失败", "拒绝", "异常", "不支持", "失效", "无法", "错误")
    out = []
    checked = 0
    for r in rows:
        a, b = (r["ca"] or "").lower(), (r["cb"] or "").lower()
        for w in pos:
            if w in a and w not in b:
                for n in neg:
                    if n in b and n not in a:
                        # crude shared-entity check: same ASCII token
                        ta = set(re.findall(r"[a-z0-9_\-]{4,}", a))
                        tb = set(re.findall(r"[a-z0-9_\-]{4,}", b))
                        common = ta & tb
                        if common:
                            out.append((r, w, n, common))
                            break
                break
        checked += 1
        if len(out) >= 5:
            break
    if not out:
        return f"**无显式冲突候选**（扫描 {checked} 对 · 这正是 conflict_reviews 常态：多数对无矛盾）\\n\\n> 生产库 82 条 conflict_reviews 记录了完整的复核留痕，规则法定位是「人工复核候选生成器」而非自动消解。"
    lines = []
    for r, w, n, common in out:
        lines.append(f"- ⚠ `{', '.join(list(common)[:3])}`：{r['uid_a'][:18]}…({r['sa']})[{w}] vs {r['uid_b'][:18]}…({r['sb']})[{n}]")
    return "\\n".join(lines) + f"\\n\\n*扫描 {checked} 对 · 演示简化算法；生产版 governance.py 是实体级极性判定 + 人工复核留痕*"

# ---------- Gradio UI ----------
import gradio as gr

with gr.Blocks(title="MemTether Demo", theme=gr.themes.Soft()) as demo:
    gr.Markdown("""# 🧠 MemTether — 跨客户端 AI 记忆中枢

**一句话**：多个异构 AI 客户端通过文件级指针共享同一份物理 SQLite —— 不做同步、不做云、不做副本。

这个 Space 跑在**全合成演示库**上（100 facts + 10 assets，由 make_demo_db.py 生成），展示四个核心链路。生产实例含真实数据，永不公开。

[GitHub](https://github.com/MemTether/MemTether) · [PyPI](https://pypi.org/project/memtether/) · Apache-2.0
""")
    with gr.Tab("🔍 检索"):
        gr.Markdown("关键词路演示（生产版是 五路召回+RRF+精排，含向量/FTS5/PPR）。**试试**：`向量`、`拒答`、`双时间轴`、`投影`、`指针`、`来源标识`、`评测`、`停用词`")
        with gr.Row():
            q_in = gr.Textbox(label="查询", placeholder="e.g. ComfyUI / STM32 / PDF")
            k_in = gr.Slider(3, 15, value=8, step=1, label="top-k")
        q_btn = gr.Button("搜索", variant="primary")
        q_out = gr.Markdown(label="结果")
        q_detail = gr.Markdown(label="全文（点开看）")
        q_btn.click(do_search, [q_in, k_in], [q_out, q_detail])
    with gr.Tab("✍️ 写入"):
        gr.Markdown("演示 gateway.remember() 的最小行为：**来源归属强制** + **双时间轴**（valid_from = recorded_at）。写入的是演示库，刷新不丢，但与生产库完全隔离。")
        with gr.Row():
            w_content = gr.Textbox(label="内容（第一句就是结论）", lines=3, placeholder="结论：本机 Clash 代理端口是 7890")
            w_type = gr.Dropdown(["fact", "decision", "experience", "incident"], value="fact", label="类型")
        with gr.Row():
            w_source = gr.Textbox(label="来源（归属强制，如 your-name）", value="visitor")
            w_tags = gr.Textbox(label="tags（可选，逗号分隔）", value="")
        w_btn = gr.Button("写入", variant="primary")
        w_out = gr.Markdown()
        w_btn.click(do_remember, [w_content, w_type, w_source, w_tags], w_out)
    with gr.Tab("📊 统计"):
        gr.Markdown("四维评分卡的口径在此库的分布（覆盖度/保鲜度/正确率/治理度详见 [hub_score](https://github.com/MemTether/MemTether)）。")
        s_btn = gr.Button("刷新统计", variant="primary")
        s_out = gr.Markdown()
        s_btn.click(do_stats, [], s_out)
        demo.load(do_stats, [], s_out)
    with gr.Tab("🔗 治理"):
        gr.Markdown("**supersession 替代链**：改口不删旧，superseded_by 串成链。**冲突检测**：规则法天花板 = 人工复核候选生成器。")
        with gr.Row():
            t_uid = gr.Textbox(label="uid（从搜索结果复制）", placeholder="fact-2026…")
            t_btn = gr.Button("查替代链")
        t_out = gr.Markdown()
        t_btn.click(do_timeline, t_uid, t_out)
        c_btn = gr.Button("扫冲突候选（简化版）")
        c_out = gr.Markdown()
        c_btn.click(do_conflicts, [], c_out)
    gr.Markdown("""---
### 为什么不用真 LLM / 真向量？

这个 Space 的目标是**让人 30 秒看懂架构差异**（文件级指针 vs 同步 vs 云），不是跑评测。向量/精排/拒答阈值需要本地模型（bge-m3 1024 维 CPU 推理），在免费 CPU Space 上会拖慢加载。完整能力请 `pip install "memtether[vector]"` 本地体验。
""")

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", 7860)))
