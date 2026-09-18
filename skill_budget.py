# -*- coding: utf-8 -*-
"""技能库预算守卫 —— 体检 / 装前试算 / 重名族 / 硬门槛。

全程只读：不写、不删、不改任何技能文件。
子命令:
  health                     体检当前技能库（注入成本 / 体积 / 重名族 / 合规）
  families                   只看重名族（职责重叠候选）
  gate                       description <= 1024 硬门槛，超限退出码 1
  preflight <dir>            装前试算：候选目录里的技能加进来要花多少
  budget                     与记忆投影槽位对比 + 线性外推
公共参数:
  --root <dir>               技能库根目录（默认中立真源）
  --cap <n>                  注入成本上限，超出退出码 1
  --json <path>              结果另存 JSON
"""
import argparse
import io
import json
import os
import re
import sys
from collections import defaultdict

# ★开源版：用 expanduser 派生，不再硬编码本机账户名（clone 后同样可用）。
#   中立真源约定：~/.agents/skills；两版客户端的 ~/.workbuddy[-ai]/skills 均为指向它的 junction。
DEFAULT_ROOT = os.path.join(os.path.expanduser('~'), '.agents', 'skills')
DESC_CAP = 1024

# ★ 必须兼容 CRLF：Windows 上 SKILL.md 多半是 \r\n，写成 \n 会读不到 frontmatter
FM = re.compile(r'^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n', re.S)
DE = re.compile(r'^description:[ \t]*(.+?)[ \t]*\r?$', re.M)
NM = re.compile(r'^name:[ \t]*(.+?)[ \t]*\r?$', re.M)

# ★踩坑（2026-09-18 实测）：DE 这条单行正则**不能**用来取 description。
#   大量 SKILL.md 用 YAML 折叠标量把描述折成多行块：
#       description: >        /  >-  /  >+  /  |  /  |-  /  |+
#         line1
#         line2
#   单行正则只会取到那个 '>' —— 实测 78 个技能里 10 个被误判成「description 只有 1 字符」，
#   既让注入成本统计严重偏低，也让「空壳检测」把正常技能误判成空壳。
#   改用 parse_desc()：整块读，按 YAML 语义折叠（> 系列换行→空格，| 系列保留换行）。
DE_HEAD = re.compile(r'^description:[ \t]*(.*?)[ \t]*\r?$', re.M)
_FOLDERS = (">", ">-", ">+", "|", "|-", "|+")


def parse_desc(fm):
    """从 frontmatter 文本取 description，兼容 YAML 块标量（多行折叠）。"""
    m = DE_HEAD.search(fm)
    if not m:
        return ""
    first = m.group(1).strip()
    # 引号包裹的字面量
    if len(first) >= 2 and first[0] == first[-1] and first[0] in "\"'":
        return first[1:-1].strip()
    if first in _FOLDERS:
        indicator, base = first, ""
    elif first:
        return first                       # 普通单行值
    else:
        indicator, base = "", ""           # description: 后为空，尝试读跨行 plain scalar
    body = []
    for ln in fm[m.end():].splitlines():
        if not ln.strip():
            if body:
                break                      # 空行 = 块结束
            continue
        if ln[0] not in (" ", "\t"):
            break                          # 缩进归零 = 块结束（下一个 key）
        body.append(ln.strip())
    if not body:
        return base
    # 客户端注入时折叠成一行算成本；'|' 系列保留换行（极少数）
    joiner = "\n" if indicator.startswith("|") else " "
    return joiner.join(body).strip()

# 客户端实际注入形态：name + description + (location: ...\SKILL.md)
# 路径随实际环境走；这里只量「location 行」带来的额外字符开销。
LOC_TPL = "(location: " + os.path.join(DEFAULT_ROOT, "%s", "SKILL.md") + ")"


def dirsize(p):
    t = 0
    for r, _d, fs in os.walk(p):
        for f in fs:
            try:
                t += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return t


def read_skill(d):
    """读一个技能目录 -> dict；无法解析返回 None"""
    sm = os.path.join(d, "SKILL.md")
    if not os.path.isfile(sm):
        return None
    txt = io.open(sm, encoding="utf-8", errors="replace").read()
    m = FM.match(txt)
    if not m:
        return None
    fm = m.group(1)
    nm = NM.search(fm)
    return {
        "dir": os.path.basename(d),
        "name": (nm.group(1).strip() if nm else os.path.basename(d)),
        "desc": parse_desc(fm),
        "size": dirsize(d),
        "files": sum(len(f) for _r, _d, f in os.walk(d)),
        "path": sm,
    }


def scan(root, nested=False):
    """扫描技能集合。nested=False: root 下每个子目录是一个技能
                      nested=True : root 下每层的子目录都当技能试读（用于解压的技能包）"""
    rows = []
    if nested:
        for dp, dns, _fns in os.walk(root):
            dns[:] = [x for x in dns if x not in (".git", "node_modules", "__pycache__")]
            for sub in dns:
                r = read_skill(os.path.join(dp, sub))
                if r and r["desc"]:
                    rows.append(r)
    else:
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue                      # 过滤迁移记录等 .json 非技能项
            r = read_skill(d)
            if r is None:
                continue
            if not r["desc"]:
                r["desc"] = ""
            rows.append(r)
    return rows


def scan_zip(zpath):
    """直接读技能包 zip 里的 SKILL.md，不落盘（147MB 的包也能秒算）"""
    import zipfile
    rows = []
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if not n.endswith("/SKILL.md"):
                continue
            parts = n.split("/")
            if len(parts) < 2:
                continue
            d = parts[-2]
            try:
                txt = z.read(n).decode("utf-8", "replace")
            except Exception:                        # noqa: BLE001
                continue
            m = FM.match(txt)
            if not m:
                continue
            fm = m.group(1)
            nm = NM.search(fm)
            rows.append({
                "dir": d,
                "name": (nm.group(1).strip() if nm else d),
                "desc": parse_desc(fm),
                "size": 0, "files": 0,
                "path": "%s!%s" % (zpath, n),
            })
    return rows


def cost_with_loc(rs):
    return sum(len(r["name"]) + len(r["desc"]) + len(LOC_TPL % r["dir"]) + 2 for r in rs)


def cost_plain(rs):
    return sum(len(r["name"]) + len(r["desc"]) for r in rs)


def families(rows):
    fam = defaultdict(list)
    for r in rows:
        tk = r["dir"].split("-")
        key = "-".join(tk[:2]) if len(tk) >= 2 else tk[0]
        fam[key].append(r["dir"])
    return sorted(((k, v) for k, v in fam.items() if len(v) >= 2),
                  key=lambda x: -len(x[1]))


def over_cap(rows):
    return [r for r in rows if len(r["desc"]) > DESC_CAP]


def hr(t="-"):
    print(t * 72)


# ---------------------------------------------------------------- health
def cmd_health(a):
    rows = scan(a.root)
    if not rows:
        print("!! 一个技能都没读到 —— 检查 --root 是否指向技能库根，或 frontmatter 是否为 CRLF")
        return 2
    c_plain, c_loc = cost_plain(rows), cost_with_loc(rows)
    fams = families(rows)
    bad = over_cap(rows)
    nosk = [r["dir"] for r in rows if not r["desc"]]

    hr("=")
    print("技能库体检   %s" % a.root)
    hr("=")
    print("技能总数        : %d" % len(rows))
    print("总体积          : %.2f MB" % (sum(r["size"] for r in rows) / 1048576))
    print("文件总数        : %d" % sum(r["files"] for r in rows))
    hr()
    print("★注入成本（name+description）              : %d 字符" % c_plain)
    print("★注入成本（含 location 行，最接近真实）    : %d 字符" % c_loc)
    print("  平均每技能    : %.0f 字符（含 location）" % (c_loc / max(1, len(rows))))
    hr()
    print("description 长度 top10:")
    for r in sorted(rows, key=lambda x: -len(x["desc"]))[:10]:
        flag = "   <<超限" if len(r["desc"]) > DESC_CAP else ""
        print("  %-42s %5d%s" % (r["dir"], len(r["desc"]), flag))
    hr()
    print("体积 top5:")
    for r in sorted(rows, key=lambda x: -x["size"])[:5]:
        print("  %-42s %8.2f MB (%d 文件)" % (r["dir"], r["size"] / 1048576, r["files"]))
    hr()
    print("重名族          : %d 族（详见 families 子命令）" % len(fams))
    for k, v in fams:
        print("  %-24s %d 个: %s" % (k, len(v), ", ".join(v[:6]) + ("…" if len(v) > 6 else "")))
    hr()
    print("无/空 description : %s" % (nosk or "无"))
    print("★不合规（desc>%d）: %d" % (DESC_CAP, len(bad)))
    for r in bad:
        print("   %-42s %d 字符" % (r["dir"], len(r["desc"])))
    hr()
    if a.cap and c_loc > a.cap:
        print("★★ 注入成本 %d 超过预算上限 %d" % (c_loc, a.cap))
        return 1
    print("预算上限 %s" % ("%d → 未超" % a.cap if a.cap else "未设"))
    return 0


# ---------------------------------------------------------------- families
def cmd_families(a):
    rows = scan(a.root)
    fams = families(rows)
    idx = {r["dir"]: r for r in rows}
    print("重名族 %d 个（前两 token 相同 = 职责重叠候选，需人工看 description 判断）" % len(fams))
    hr("=")
    for k, v in fams:
        print("【%s】%d 个" % (k, len(v)))
        for m in v:
            r = idx[m]
            print("  - %-46s desc %4d 字符  %6.2f MB"
                  % (m, len(r["desc"]), r["size"] / 1048576))
            d = r["desc"].replace("\n", " ")
            print("      %s" % (d[:220] + ("…" if len(d) > 220 else "")))
        print()
    return 0


# ---------------------------------------------------------------- gate
def cmd_gate(a):
    rows = scan(a.root)
    bad = over_cap(rows)
    if not bad:
        print("[OK] %d 个技能，description 全部 <= %d 字符" % (len(rows), DESC_CAP))
        return 0
    print("[FAIL] %d 个技能 description 超限（>%d）：" % (len(bad), DESC_CAP))
    for r in bad:
        print("  %-42s %5d 字符  → %s" % (r["dir"], len(r["desc"]), r["path"]))
    return 1


# ---------------------------------------------------------------- preflight
def cmd_preflight(a):
    cand_root = a.dir
    if cand_root.lower().endswith(".zip"):
        if not os.path.isfile(cand_root):
            print("!! zip 不存在: %s" % cand_root)
            return 2
        cands = scan_zip(cand_root)
    elif not os.path.isdir(cand_root):
        print("!! 目录不存在: %s" % cand_root)
        return 2
    else:
        cands = []
    cur = scan(a.root)
    if not cands:
        cands = scan(cand_root, nested=True)
    if not cands:
        print("!! 在 %s 下没读到任何带 frontmatter 的 SKILL.md" % cand_root)
        print("   若是解压的技能包，确认目录结构为 <任意>/<技能名>/SKILL.md")
        return 2

    cur_names = {r["dir"] for r in cur}
    new = [r for r in cands if r["dir"] not in cur_names]
    dup = [r for r in cands if r["dir"] in cur_names]

    c_new = cost_with_loc(new)
    c_all = cost_with_loc(cur) + c_new
    avg = c_new / max(1, len(new))
    bad = over_cap(new)

    hr("=")
    print("装前试算   %s" % cand_root)
    hr("=")
    print("候选技能        : %d 个（其中已存在 %d 个，实际新增 %d 个）"
          % (len(cands), len(dup), len(new)))
    print("★新增注入成本   : %+d 字符（含 location 行，均摊 %.0f/个）" % (c_new, avg))
    print("  当前库         : %d 字符 / %d 个" % (cost_with_loc(cur), len(cur)))
    print("  装后合计       : %d 字符 / %d 个" % (c_all, len(cur) + len(new)))
    if a.mem_slot:
        print("  是记忆投影槽位的 %.1f 倍（槽位 %d）" % (c_all / a.mem_slot, a.mem_slot))
    hr()
    print("增量 top10（最贵的先砍）：")
    for r in sorted(new, key=lambda x: -(len(x["name"]) + len(x["desc"])))[:10]:
        c = len(r["name"]) + len(r["desc"]) + len(LOC_TPL % r["dir"]) + 2
        print("  %-46s %5d 字符" % (r["dir"], c))
    hr()
    if bad:
        print("★超限（desc>%d）：%d 个 —— 装之前先压这些" % (DESC_CAP, len(bad)))
        save = 0
        for r in bad:
            print("   %-44s %5d → 压到 600 可省 %d 字符"
                  % (r["dir"], len(r["desc"]), len(r["desc"]) - 600))
            save += max(0, len(r["desc"]) - 600)
        print("  压完共省 %d 字符 ≈ 抵消 %.1f 个新技能" % (save, save / max(1, avg)))
    else:
        print("description 合规：%d 个全部 <= %d 字符" % (len(new), DESC_CAP))
    if dup:
        hr()
        print("已存在（不会重复计入）：%s" % ", ".join(sorted(x["dir"] for x in dup)[:8]))
    hr()
    if a.cap and c_new > a.cap:
        print("★★ 新增成本 %d 超过 --cap %d，建议分批装或先压 description" % (c_new, a.cap))
        return 1
    print("结论：新增 %d 个技能需 %d 字符；治理杠杆在超限项与超长 description，不在数量。"
          % (len(new), c_new))
    return 0


# ---------------------------------------------------------------- budget
def cmd_budget(a):
    rows = scan(a.root)
    c_all = cost_with_loc(rows)
    avg = c_all / max(1, len(rows))
    slot, used = a.mem_slot, a.mem_used
    hr("=")
    print("技能列表 vs 记忆投影")
    hr("=")
    print("  技能列表    : %7d 字符 / %d 个   无硬顶，每轮全量进 system prompt"
          % (c_all, len(rows)))
    print("  记忆投影槽位: %7d 字符（已用 %d，余 %d）★硬顶，超了整体截断"
          % (slot, used, max(0, slot - used)))
    print("  ★倍数      : %.1f 倍" % (c_all / slot))
    hr()
    print("  按当前均值 %.0f 字符/个线性外推：" % avg)
    for n in (100, 150, 200, 400, 800, 1642):
        c = int(avg * n)
        print("    %5d 个 -> %8d 字符 (%6.1f KB)   是记忆投影的 %6.1f 倍"
              % (n, c, c / 1024.0, c / slot))
    hr()
    print("  结论：两者不是同一个池子 —— 技能不挤记忆投影；")
    print("        但技能列表无硬顶无闸门，是独立压力点，需自己设 cap。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="技能库预算守卫（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--root", default=DEFAULT_ROOT)
        p.add_argument("--cap", type=int, default=0)
        p.add_argument("--json", dest="json_out", default="")
        p.add_argument("--mem-slot", type=int, default=3980)
        p.add_argument("--mem-used", type=int, default=0)

    p = sub.add_parser("health", help="体检")
    common(p)
    p = sub.add_parser("families", help="重名族")
    common(p)
    p = sub.add_parser("gate", help="硬门槛")
    common(p)
    p = sub.add_parser("preflight", help="装前试算")
    common(p)
    p.add_argument("dir")
    p = sub.add_parser("budget", help="与记忆投影对比")
    common(p)

    a = ap.parse_args()
    fn = {"health": cmd_health, "families": cmd_families, "gate": cmd_gate,
          "preflight": cmd_preflight, "budget": cmd_budget}[a.cmd]
    rc = fn(a)
    if a.json_out and a.cmd in ("health", "preflight"):
        rows = scan(a.root)
        json.dump({"rows": [{k: v for k, v in r.items()} for r in rows],
                   "cost_with_loc": cost_with_loc(rows)},
                  io.open(a.json_out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("\n[JSON] %s" % a.json_out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
