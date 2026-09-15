# -*- coding: utf-8 -*-
"""
skill_forge.py — 技能自动沉淀闭环（Trace2Skill 式五步）

Initialize → Execute/Diagnose → Patch → Verify → Admit

    scan                 扫描中枢里的 experience/incident，按主题聚类，列出**可沉淀**主题
    draft <topic>        把该主题下的记录打包成草稿（默认素材包，--auto 调 LLM 直接生成 SKILL.md）
    admit <name>         校验草稿（frontmatter 合规 / desc≤1024 / 正文<500行）→ 入库 → git commit
    status               看候选、草稿、已入库三态

设计原则（来自 Trace2Skill / EvoSkill / SkillForge 实证）：
  1. **批量归纳**：攒够 ≥2 条同主题记录才沉淀，绝不来一条改一行。
  2. **验证门控**：admit 前强制过校验，不合格不许入库（脏技能会污染后续所有任务）。
  3. **人在环路**：LLM 只生成草稿，落盘必须显式 admit（可回滚，git 有历史）。
  4. **只改 artifact**：不碰模型权重，沉淀物是 SKILL.md 文档。
"""
import os
import re
import sys
import json
import shutil
import subprocess

HUB = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HUB)
import gateway  # noqa: E402

SKILLS = r'<PATH>\.agents\skills'
CAND = r'<PATH>\.agents\.candidates'
STATE = os.path.join(HUB, 'skill_forge_state.json')

# 主题词表：命中 ≥MIN_HITS 条记录才建议沉淀
TOPICS = {
    'windows-electron': ['electron', 'asar', '任务栏', '托盘', '图标', '窗口', '快捷方式'],
    'windows-automation': ['uia', '自动化', '进程', '任务计划', '自启', '黑窗', 'powershell', '计划任务'],
    'network-tunnel': ['vpn', 'tun', '代理', '3002', 'econnreset', 'econn', '端口', '网络', '超时'],
    'memory-skill': ['记忆', '中枢', 'skill', '技能', '投影', 'symlink', 'junction', 'memory.db', 'gateway'],
    'model-diagnostics': ['模型', '上下文', 'token', '视觉', '多模态', '推理', '试用', '额度'],
    'web-verify': ['截图', 'headless', 'svg', '渲染', '预览', 'edge', '无头', 'css'],
    'android-apk': ['apk', '安卓', '包名', '权限', '反编译'],
    'image-gen': ['comfyui', '生图', '绘图', 'flux', 'swarmui', '出图'],
    'stm32': ['stm32', 'cubeide', 'cube', '烧录', ' hal ', 'gpio'],
}
MIN_HITS = 2


def _load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding='utf-8'))
        except Exception:
            pass
    return {'admitted': {}, 'dismissed': []}


def _save_state(s):
    json.dump(s, open(STATE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


def _candidates():
    conn = gateway.get_conn()
    try:
        rows = conn.execute(
            "SELECT uid,type,source,content,updated_at FROM facts "
            "WHERE status='active' AND type IN ('experience','incident') "
            "ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def cmd_scan(_args=None):
    st = _load_state()
    done = set(st['admitted'].get('uids', [])) | set(st.get('dismissed', []))
    rows = [r for r in _candidates() if r['uid'] not in done]
    buckets = {}
    for r in rows:
        text = (r['content'] or '').lower()
        for topic, kws in TOPICS.items():
            if any(k.lower() in text for k in kws):
                buckets.setdefault(topic, []).append(r)
    print('候选记录 %d 条（未沉淀）' % len(rows))
    print()
    print('%-22s %5s  %s' % ('主题', '条数', '是否可沉淀'))
    print('-' * 60)
    ready = []
    for topic, items in sorted(buckets.items(), key=lambda x: -len(x[1])):
        ok = len(items) >= MIN_HITS
        print('%-22s %5d  %s' % (topic, len(items), '可沉淀 → draft ' + topic if ok else '再攒 %d 条' % (MIN_HITS - len(items))))
        if ok:
            ready.append(topic)
    if not ready:
        print()
        print('（暂无达到 %d 条的主题。批量归纳优于逐条沉淀，攒够再来。）' % MIN_HITS)
    return ready


def cmd_draft(args):
    topic = args[0] if args else None
    auto = '--auto' in args
    if not topic:
        print('用法: skill_forge.py draft <topic> [--auto]')
        return
    st = _load_state()
    done = set(st['admitted'].get('uids', [])) | set(st.get('dismissed', []))
    kws = TOPICS.get(topic)
    if not kws:
        print('未知主题: %s（可用: %s）' % (topic, ', '.join(TOPICS)))
        return
    items = [r for r in _candidates()
             if r['uid'] not in done and any(k.lower() in (r['content'] or '').lower() for k in kws)]
    if len(items) < MIN_HITS:
        print('该主题只有 %d 条，不足 %d 条，暂不沉淀。' % (len(items), MIN_HITS))
        return

    os.makedirs(os.path.join(CAND, topic), exist_ok=True)
    src = os.path.join(CAND, topic, '_sources.md')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('# 素材：%s（%d 条记录）\n\n' % (topic, len(items)))
        for it in items:
            f.write('## [%s|%s] %s\n\n%s\n\n' % (it['type'], it['source'], (it['updated_at'] or '')[:16], it['content']))

    body = None
    if auto:
        prompt = (
            '下面是同一主题下 agent 的实战记录（踩坑与解法），共 %d 条。\n'
            '请归纳成一份可复用的 SKILL.md。要求：\n'
            '1) 只保留被 2 条以上记录共同支持的做法，偶发情况丢弃；\n'
            '2) 写 SOP，不写流水账；必须包含「适用时机 / 操作步骤 / 已踩过的坑 / 验证方法」四节；\n'
            '3) description 用第三人称，同时写清「做什么」和「什么时候用」，≤1024 字符；\n'
            '4) name 只能小写字母/数字/连字符，≤64 字符。\n'
            '输出严格 JSON: {"name": "...", "description": "...", "body": "..."}（body 不含 frontmatter）\n\n'
            '%s'
        ) % (len(items), '\n\n'.join('---\n' + (i['content'] or '')[:1600] for i in items))
        # 通道回退：deepseek 便宜优先，401/失败再走 astra（实测 deepseek key 已失效）
        r = None
        for m in ('deepseek', 'astra'):
            got = gateway._llm_json(prompt, system='你是技能库管理员，擅长把实战经验提炼成可复用 SOP。',
                                    model=m, max_tokens=2600)
            if got and got.get('name') and got.get('body'):
                r = got
                print('[LLM] 使用通道: %s' % m)
                break
            if got and got.get('_error'):
                print('[warn] %s 通道失败(%s)，换下一个' % (m, str(got['_error'])[:40]))
        if r:
            body = r
        else:
            print('[warn] LLM 生成失败，回退为素材包模式（草稿需人工撰写）')

    out = os.path.join(CAND, topic, 'SKILL.md')
    if body:
        text = '---\nname: %s\ndescription: %s\n---\n\n%s\n' % (
            body['name'], body['description'], body['body'])
    else:
        text = (
            '---\nname: %s\ndescription: 【待填写，第三人称，写清做什么+什么时候用】%s 相关……\n---\n\n'
            '# %s\n\n## 适用时机\n（待填写）\n\n## 操作步骤\n（待填写）\n\n'
            '## 已踩过的坑\n（见 _sources.md，共 %d 条记录）\n\n## 验证方法\n（待填写）\n'
        ) % (topic.replace('_', '-'), topic, topic, len(items))
    open(out, 'w', encoding='utf-8').write(text)
    print('草稿已生成: %s' % out)
    print('素材:       %s' % src)
    print('下一步:     python skill_forge.py admit %s' % topic)
    return out


def _parse_front(text):
    m = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if not m:
        return None, ['缺少 YAML frontmatter']
    fm = m.group(1)
    errs = []
    name = re.search(r'^name:\s*(.+)$', fm, re.M)
    desc = re.search(r'^description:\s*(.+)$', fm, re.M)
    name = name.group(1).strip() if name else ''
    desc = desc.group(1).strip() if desc else ''
    if not name:
        errs.append('缺 name')
    elif len(name) > 64:
        errs.append('name 超 64 字符')
    elif not re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', name):
        errs.append('name 只能是小写字母/数字/连字符')
    if not desc:
        errs.append('缺 description')
    elif len(desc) > 1024:
        errs.append('description 超 1024 字符（当前 %d）' % len(desc))
    elif '【待填写' in desc:
        errs.append('description 还是占位符')
    body_lines = text[m.end():].count('\n') + 1
    if body_lines > 500:
        errs.append('正文超 500 行（当前 %d），应拆到 references/' % body_lines)
    if '（待填写）' in text:
        errs.append('正文仍有「待填写」占位')
    return {'name': name, 'desc': desc, 'lines': body_lines}, errs


def cmd_admit(args):
    topic = args[0] if args else None
    if not topic:
        print('用法: skill_forge.py admit <topic>')
        return
    src = os.path.join(CAND, topic, 'SKILL.md')
    if not os.path.exists(src):
        print('草稿不存在: %s' % src)
        return
    text = open(src, encoding='utf-8').read()
    meta, errs = _parse_front(text)
    if errs:
        print('校验未通过，拒绝入库：')
        for e in errs:
            print('  - ' + e)
        return
    name = meta['name']
    dst_dir = os.path.join(SKILLS, name)
    if os.path.exists(dst_dir):
        print('已存在同名技能: %s（先人工合并，不覆盖）' % dst_dir)
        return
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dst_dir, 'SKILL.md'))
    for extra in ('_sources.md',):
        p = os.path.join(CAND, topic, extra)
        if os.path.exists(p):
            os.makedirs(os.path.join(dst_dir, 'references'), exist_ok=True)
            shutil.copy2(p, os.path.join(dst_dir, 'references', extra))

    st = _load_state()
    uids = st['admitted'].get('uids', [])
    kws = TOPICS.get(topic, [])
    for r in _candidates():
        if any(k.lower() in (r['content'] or '').lower() for k in kws):
            uids.append(r['uid'])
    st['admitted'] = {'uids': sorted(set(uids)), 'last_topic': topic, 'last_name': name}
    st.setdefault('history', []).append({'topic': topic, 'name': name, 'ts': gateway.__dict__.get('_now', '')})
    _save_state(st)

    try:
        subprocess.run(['git', 'add', '-A'], cwd=r'<PATH>\.agents', check=True)
        subprocess.run(['git', '-c', 'user.name=memory-hub', '-c', 'user.email=local@hub',
                        'commit', '-q', '-m', 'skill: %s（自动沉淀自 %s）' % (name, topic)],
                       cwd=r'<PATH>\.agents', check=True)
        print('已 git commit')
    except Exception as e:
        print('[warn] git 提交失败: %s' % e)
    print('入库完成: %s' % os.path.join(dst_dir, 'SKILL.md'))
    print('（两版通过 junction 立即可见；回滚: cd <PATH>\\.agents && git revert HEAD）')


def cmd_status(_args=None):
    st = _load_state()
    n_cand = len(_candidates())
    n_done = len(set(st['admitted'].get('uids', [])))
    drafts = sorted(os.listdir(CAND)) if os.path.exists(CAND) else []
    skills = sorted(d for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d)))
    print('候选记录 : %d 条（已沉淀 %d 条）' % (n_cand, n_done))
    print('待审草稿 : %s' % (', '.join(drafts) or '无'))
    print('已入库   : %d 个技能' % len(skills))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    args = sys.argv[2:]
    {'scan': cmd_scan, 'draft': cmd_draft, 'admit': cmd_admit, 'status': cmd_status}.get(
        cmd, lambda a: print('未知子命令: %s' % cmd))(args)
