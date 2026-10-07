# -*- coding: utf-8 -*-
"""scripts/zh_calibration.py — a49: Chinese threshold calibration for absorb

Uses the WB2API free pool (deepseek-v4-flash) to generate paraphrase /
update / unrelated Chinese fact pairs, embeds them with the same
embed_local model absorb uses, and reports the similarity distribution
per class so the thresholds are data-driven, not vibes.
Run: python scripts/zh_calibration.py [--n 20]
"""
import itertools, json, os, sys, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

PROMPT = """生成{n}组中文"记忆事实"句子对，用于测试记忆去重系统。每组是一个JSON对象：
{{"a": "...", "b": "...", "label": "..."}}
label 必须是以下三选一：
- "paraphrase": b 是 a 的同义改写（同一个事实）
- "update": b 更新了 a 的某个细节（如数字、时间、状态变化）
- "unrelated": b 与 a 完全无关
要求：句子要像 AI 记忆库里的真实条目（工具配置、用户偏好、项目事实）。
只输出 JSON 数组，不要其他文字。"""


def generate(n=20):
    cfg_paths = [
        os.path.expanduser(r"~\workbuddy2api-panel\config.json"),
        r"E:\RUANJIAN\workbuddy2api-panel\config.json",
    ]
    api_key = None
    for p in cfg_paths:
        if os.path.exists(p):
            cfg = json.load(open(p, encoding="utf-8"))
            api_key = cfg.get("api_key")
            break
    if not api_key:
        print("[warn] WB2API config not found; using built-in probe set")
        return None
    body = json.dumps({
        "model": "cn:deepseek-v4-flash",
        "messages": [{"role": "user", "content": PROMPT.format(n=n)}],
        "temperature": 1.0,
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:7874/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    r = json.load(urllib.request.urlopen(req, timeout=120))
    text = r["choices"][0]["message"]["content"]
    start, end = text.find("["), text.rfind("]") + 1
    return json.loads(text[start:end])


BUILTIN = [
    {"a": "用户的代理端口是7890", "b": "用户把代理端口改成了7897", "label": "update"},
    {"a": "项目用 SQLite 存记忆", "b": "项目的记忆存储是 SQLite", "label": "paraphrase"},
    {"a": "用户喜欢喝美式咖啡", "b": "用户不喜欢喝拿铁", "label": "unrelated"},
    {"a": "会议定在周三下午", "b": "会议改到周五下午了", "label": "update"},
    {"a": "STM32 用 SysTick 做延时", "b": "开发板的延时函数基于 SysTick", "label": "paraphrase"},
    {"a": "API 密钥存在 E 盘加密目录", "b": "冰箱里的牛奶过期了", "label": "unrelated"},
    {"a": "模型温度设为 0.3", "b": "模型温度调到 0.9", "label": "update"},
    {"a": "部署在 8820 端口", "b": "服务的监听端口是 8820", "label": "paraphrase"},
]


def main():
    n = 20
    if "--n" in sys.argv:
        n = int(sys.argv[sys.argv.index("--n") + 1])
    pairs = generate(n) or []
    pairs = pairs + BUILTIN
    from embed_local import encode
    import math

    def cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0

    texts = []
    for p in pairs:
        texts += [p["a"], p["b"]]
    vecs = encode(texts)
    sims = {"paraphrase": [], "update": [], "unrelated": []}
    for i, p in enumerate(pairs):
        s = cos(vecs[i * 2], vecs[i * 2 + 1])
        sims[p["label"]].append(s)
    print(f"\n=== 中文阈值校准 ({len(pairs)} pairs) ===")
    stats = {}
    for label, arr in sims.items():
        if not arr:
            continue
        lo, hi = min(arr), max(arr)
        avg = sum(arr) / len(arr)
        stats[label] = (lo, hi, avg)
        print(f"{label:12s} n={len(arr):3d}  min={lo:.3f} max={hi:.3f} avg={avg:.3f}")
    if all(k in stats for k in ("paraphrase", "update", "unrelated")):
        p_hi = stats["paraphrase"][0]
        u_lo = stats["update"][1]
        u_min = stats["update"][0]
        r_max = stats["unrelated"][2]
        print(f"\n建议: duplicate >= {p_hi:.2f} (paraphrase 最小值)")
        print(f"      related    >= {max(u_min, r_max) + 0.02:.2f} (update 最小/unrelated 均值较大者+0.02)")
        print(f"      update 区间: [{max(u_min, r_max) + 0.02:.2f}, {p_hi:.2f})")
        print(f"      注: update 类里含数字变更的，由数字守卫兜底，不依赖阈值")
    out = os.path.join(REPO, "_dev", "zh_calibration_result.json")
    json.dump(sims, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
