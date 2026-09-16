"""make_demo_db.py —— 生成对外发布的**全合成**演示库。

为什么不能用真库脱敏
--------------------
真库里含真实姓名与校内标识、本机账号路径、密钥凭证、安全事件、联系方式等多类
敏感信息（合计近百处）。逐条打码有两个问题：
  ① **组合可推断**：即使字段各自脱敏，"某台机器 + 某个时间 + 某类事件"仍能定位到人；
  ② **措辞会泄露**：条目的行文习惯、关注的议题本身就是画像。
所以对外只给**合成库**：结构照真库（同 schema、同类型分布、同时间跨度），
内容全部虚构。演示效果靠"结构多样性"，不靠"真事"。

可复现性
--------
随机种子固定（SEED），同一版本脚本产出**逐字节相同**的库。
使用者 clone 后跑 `python scripts/make_demo_db.py` 即得，无需下载二进制。

用法
----
    python scripts/make_demo_db.py                 # 输出 demo/memory_demo.db
    python scripts/make_demo_db.py --out /tmp/x.db # 指定输出
    python scripts/make_demo_db.py --selfcheck     # 只自检不写盘
"""
import argparse
import io
import json
import os
import random
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SEED = 20260916

# ----------------------------------------------------------------- 敏感词外置
# ★2026-09-16：真名 / 安全事件词改为**外部加载**（与 scripts/scan_leaks.py 同一份词表）。
#   动机：本文件是要开源发布的。把真名写死在自检正则里 = **用泄密清单去泄密**
#   —— 实测它自己就贡献了 1 处 BLOCK 命中（`(r'<真名>', '真实姓名')`），
#     而 `scan_leaks.py` 早就因为同一个原因改成外置了，这里当时漏改。
#   词表缺失 → 这几类降级为空（只跑通用规则），不报错，也不假装"很干净"。
_TERMS = os.path.join(HERE, 'leak_terms.local.json')


def _local_terms():
    try:
        with open(_TERMS, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return [], []
    pick = lambda k: [x for x in (d.get(k) or []) if isinstance(x, str) and x]
    return pick('names'), pick('incident_patterns')


_NAMES, _INCIDENTS = _local_terms()

# ----------------------------------------------------------------- 虚构词表
# 全部为编造名称：客户端、工具、路径、账号。刻意与真实产品名保持距离。
CLIENTS = ['alphachat', 'betamind', 'gammacli', 'deltahub', 'epsilon_ai']
AGENTS = ['planner', 'runner', 'reviewer', 'reflector', 'scribe']
FAKE_USERS = ['user_a', 'user_b', 'user_c']
FAKE_PATHS = [
    '/opt/demo/tools/editor',
    '/opt/demo/tools/archiver',
    '/opt/demo/suite/terminal',
    '/srv/demo/stack/worker',
    '/srv/demo/stack/scheduler',
]
FAKE_TOOLS = [
    ('DemoEditor', '轻量文本编辑器', '编辑 / 批量替换'),
    ('DemoArchiver', '归档压缩工具', '打包 / 解包 / 校验'),
    ('DemoTerminal', '多标签终端', '分屏 / 会话恢复'),
    ('DemoStack', '本地服务编排', '拉起 / 重启 / 看日志'),
    ('DemoScheduler', '定时任务管理', '计划任务 / 触发排查'),
    ('DemoVault', '凭据保管箱', '加密存储 / 轮换提醒'),
    ('DemoProbe', '网络探测', '连通性 / 延迟'),
    ('DemoMirror', '目录镜像', '单向同步 / 差异比对'),
    ('DemoLint', '配置校验', '语法 / 规则检查'),
    ('DemoTrace', '调用链追踪', '日志聚合 / 耗时归因'),
]

# ----------------------------------------------------------------- 合成条目
# 分六组，覆盖真实库里的类型分布（experience 最多、incident 最少）。
# 每条都是"结构像真的、内容全假"的独立句，不复述任何真实事件。
T_EXPERIENCE = [
    '结论：切换客户端后若发现检索变慢，先看是否退化成纯关键词路径——向量栈缺失时不会报错，只会静默降级。',
    '结论：同一份记忆被多个客户端读取时，写入必须带来源标识，否则归属会算到别的客户端头上。',
    '结论：判断内容多不多要看字符数，不能看字节数；中英混排下字节数约为字符数的 1.5 倍。',
    '结论：跨客户端共享一律用文件级指针，不做复制；复制会漂移，指针不会。',
    '结论：给检索加拒答能力时，默认必须是"照常返回 + 加提示"，直接拒答会让读者误以为库里真的没有。',
    '结论：词形匹配对"同形不同属性"的提问原理上无解——库里有这个实体，但没有被问的那个属性。',
    '结论：评测集饱和后就失去鉴别力，此时换模型看不出差别，必须换改述式题集。',
    '结论：语义路要固定后端与维度，换模型必须重建索引，否则向量集合不可比。',
    '结论：短进程模式下每个查询都要重新加载模型，一次问多个问题可以摊薄冷启动。',
    '结论：临时脚本里含明文凭据的，绝不能提交；提交前必须用独立规则集复扫。',
    '结论：同一件事只存一处，存两处必然漂移。指针要指向唯一真源。',
    '结论：投影超预算的后果是整体截断而不是截掉末尾，所以必须留余量并显式告警。',
    '结论：新工作区开箱即用的关键是"与工作区无关"的注入槽位，靠父目录放文件覆盖不到。',
    '结论：共享什么取决于"用户为什么装第二个实例"，不是"什么能共享"。',
]
T_INCIDENT = [
    '巡检发现投影超限：3904 / 3900 字符——超限是静默整体截断，注入时会丢内容。',
    '一次同步把人工维护的说明文件推平成模板版，事后从备份还原；根因是保护名单判据不一致。',
    '扫描器报出两百余条假阳性，根因是跨模块复用常量时字段解包顺序写错。',
    '历史扫描显示旧提交里有明文路径，工作区却干净——问题在历史，不在当前文件。',
    '客户端重启后配置未生效，根因是审批记录有内存缓存，改文件在同进程内不会被重读。',
    '镜像脚本只覆盖了一半实例的工作区，自检却全绿——检查清单本身漏了对象。',
]
T_FACT = [
    '演示库的字符预算按字符数计，不是字节数；官方槽位是硬上限。',
    '记忆分四类：事实 / 决策 / 事故 / 经验。经验与事故最容易在裁剪时被饿死，需保底。',
    '工具资产表记录名称、别名、入口、能力与已知问题，用于任务前取配方。',
    '配方表按任务模式匹配，给出首选工具与已知坑；本演示库中该表为空。',
    '检索走混合路径：向量 + 精确串 + 字面，任一路失败会退化，退化不抛异常。',
    '来源标识用于区分是哪个客户端写入的，取值来自注册表。',
    '双时间轴区分"某时刻什么为真"与"系统当时认为什么为真"。',
    '投影是给注入侧看的精简版，真源始终是数据库文件。',
    '索引元数据里记录了向量维度，维度不一致会硬报错而不是静默出错。',
    '停用词表由本机语料频次自动生成，语料变化后需重建。',
    '拒答判据的两个阈值改任何一处都必须重跑标定，复核零误拒是否仍成立。',
    '发布链路有四段闸门：同步前自检、清单闸门、后置守卫、同步后独立复扫。',
    '本演示库由脚本合成，不含任何真实数据；种子固定，产出可复现。',
    '工具资产的路径在演示库中一律为虚构路径，避免暴露真实安装位置。',
]
T_DECISION = [
    '决定：拒答能力默认只加提示不隐藏结果，真正拒答需显式开启。',
    '决定：向量后端默认走本地模型，不依赖外部接口；资源占用以不常驻为原则。',
    '决定：对外只发布合成演示库，真库永不出仓。',
    '决定：评测不自报分数、不与别家比数值，只公开评分口径与失真警告。',
    '决定：共享用指针而非复制，避免两处状态漂移。',
    '决定：许可采用宽松许可，避免使用者顾虑。',
    '决定：临时脚本与含凭据的文件一律不提交，靠独立规则集兜底。',
    '决定：只发布引擎与演示数据，真实记忆数据不随仓库分发。',
]

# 生成规格：组 → 条数。合计 100 条，分布刻意模仿真实库（经验最多、事故最少）。
PLAN = [
    (T_EXPERIENCE, 'experience', 34),
    (T_FACT, 'fact', 28),
    (T_DECISION, 'decision', 22),
    (T_INCIDENT, 'incident', 16),
]

FAKE_TAGS = ['demo', 'memory', 'retrieval', 'sync', 'ops', 'bench', 'proj', 'perf']
FAKE_SCOPE = ['demo', 'project', 'global']


def _ts(rng, day_lo=1, day_hi=28):
    """造一个 2026-08 月内的时间戳（演示库统一用一个月，便于阅读）。"""
    return '2026-08-%02d %02d:%02d:%02d' % (
        rng.randint(day_lo, day_hi), rng.randint(0, 23),
        rng.randint(0, 59), rng.randint(0, 59))


def load_schema():
    """从 gateway.py 源码里取出 SCHEMA 字面量 —— **唯一真源，禁止手抄**。

    ★为什么必须这么做（2026-09-16 实测踩到）：
      本脚本最初把 facts/tool_assets 的建表语句手抄了一份，结果 tool_assets 漏了
      `type` / `recipe_ids` / `prerequisites` / `last_verified_at` / `verification_method`
      五列。演示库本身能建、能插入、自检也全绿，**直到跑语义检索时才炸**：
        memsearch.asset_text() → `row['prerequisites']` → IndexError
      —— 属"跑起来不报错、但结果错/半路炸"的一类，靠肉眼对 schema 是发现不了的。
      现在改成从 gateway.py 抽 SCHEMA 字符串，schema 一改这里自动跟随。

    ★不 import gateway 的原因：gateway 在 import 期会连库/建表（init_db），
      而本脚本的职责是"造一个干净的演示库"，不应有任何副作用。
      所以用正则从源码里抽字面量，抽不到就**硬报错**（不静默降级）。
    """
    src_path = os.path.join(ROOT, 'gateway.py')
    with open(src_path, encoding='utf-8') as f:
        src = f.read()
    m = re.search(r'^SCHEMA\s*=\s*"""(.*?)"""', src, re.S | re.M)
    if not m:
        raise SystemExit('★无法从 %s 抽到 SCHEMA 字面量 —— '
                         'gateway.py 的 SCHEMA 定义形式变了，请同步更新本函数'
                         % src_path)
    return m.group(1)


def build(out_path, seed=SEED):
    rng = random.Random(seed)
    if os.path.exists(out_path):
        os.remove(out_path)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    con = sqlite3.connect(out_path)
    cur = con.cursor()
    # ★建表语句直接来自 gateway.py（唯一真源），不再手抄 —— 见 load_schema() 的踩坑说明。
    #   只用到 facts / tool_assets / recipes / audit_log 四张表，SCHEMA 里其余表建了留空，
    #   与真库"表齐全但多数为空"的形态一致，便于同一套读取代码直接跑。
    cur.executescript(load_schema())

    n_fact = 0
    for pool, ftype, want in PLAN:
        # 池子不够就循环取（确定性），保证条数精确、内容不重复取同一句两次以上
        picks = []
        while len(picks) < want:
            batch = list(pool)
            rng.shuffle(batch)
            picks.extend(batch)
        for i, content in enumerate(picks[:want]):
            n_fact += 1
            uid = 'demo-%s-%04d' % (ftype, n_fact)
            src = rng.choice(CLIENTS)
            ts = _ts(rng)
            cur.execute(
                'INSERT INTO facts (uid,type,subject,content,status,source,scope,'
                'confidence,tags,created_at,updated_at,recorded_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (uid, ftype, content.split('：')[0][:24], content, 'active', src,
                 rng.choice(FAKE_SCOPE), round(rng.uniform(0.7, 1.0), 2),
                 ','.join(rng.sample(FAKE_TAGS, rng.randint(1, 3))),
                 ts, ts, ts))

    # 工具资产：10 个虚构工具，路径全为虚构
    # ★type 必须给值：真库用 local_tool/remote_service/endpoint/cli/script/folder 分类，
    #   留 NULL 会让"按类型筛资产"这类读取路径在演示库上得到与真库不同的行为。
    for i, (name, cap, alias) in enumerate(FAKE_TOOLS, 1):
        ts = _ts(rng)
        cur.execute(
            'INSERT INTO tool_assets (uid,name,aliases,type,path,entrypoint,capabilities,'
            'known_failures,prerequisites,status,source,created_at,updated_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('demo-tool-%03d' % i, name, alias,
             rng.choice(('local_tool', 'remote_service', 'cli', 'script')),
             FAKE_PATHS[i % len(FAKE_PATHS)],
             name.lower() + '-cli', cap, '', '', 'active', rng.choice(CLIENTS), ts, ts))

    # 配方表：留空是有意的 —— 真库里也为 0 行，正好演示"表存在但无数据"的处理
    for i in range(12):
        cur.execute(
            'INSERT INTO audit_log (op,target,agent,detail,ts) VALUES (?,?,?,?,?)',
            (rng.choice(['remember', 'rebuild', 'search', 'sync']),
             'demo-%04d' % rng.randint(1, 100), rng.choice(AGENTS),
             'demo seed', _ts(rng)))
    con.commit()
    counts = {t: cur.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
              for t in ('facts', 'tool_assets', 'recipes', 'audit_log')}
    con.close()
    return counts


def schema_parity(out_path):
    """★表结构一致性检查：演示库的列集必须与 gateway.SCHEMA 完全一致。

    动机（2026-09-16 实测）：本脚本曾手抄 schema，tool_assets 漏 5 列，
      库能建、能插入、旧版自检全绿，**直到跑语义检索才 IndexError**。
      "建得出来"不等于"读得动" —— 所以这里逐表比对列集，不等运行期才发现。
    返回 (ok, 差异列表)。
    """
    def cols_of(schema_text):
        out = {}
        for m in re.finditer(r'CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);',
                             schema_text, re.S):
            tbl, body = m.group(1), m.group(2)
            cs = []
            for line in body.split('\n'):
                line = line.strip()
                if not line or line.startswith('--') or line.upper().startswith(('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE(')):
                    continue
                cs.append(line.split()[0])
            out[tbl] = cs
        return out

    want = cols_of(load_schema())
    con = sqlite3.connect(out_path)
    have = {t: [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    bad = []
    for tbl, wc in want.items():
        hc = have.get(tbl)
        if hc is None:
            bad.append('%s：演示库缺整张表' % tbl)
        elif set(wc) != set(hc):
            miss = sorted(set(wc) - set(hc))
            extra = sorted(set(hc) - set(wc))
            bad.append('%s：缺列 %s / 多列 %s' % (tbl, miss or '无', extra or '无'))
    return (not bad), bad


def smoke(out_path):
    """★功能冒烟：用**真实读取路径**跑一遍演示库，而不是只看行数。

    这一项就是当初漏掉那个 bug 的地方 —— schema 对不上时，
      gateway.stats 照样返回、memsearch.asset_text 才炸。
      所以必须真的调一次资产文本组装与一次检索。
    子进程 + MEM_DB 环境变量，确保被测的就是"用户 clone 后跑的那条路"。
    """
    code = (
        'import os,sys,json\n'
        'import gateway, memsearch\n'
        'assert os.path.samefile(gateway.DB, os.environ["MEM_DB"]), "gateway 未走演示库"\n'
        'assert os.path.samefile(memsearch.DB, os.environ["MEM_DB"]), "memsearch 未走演示库"\n'
        'st = gateway.stats()\n'
        'assert st["facts"] > 0 and st["tool_assets"] > 0, "演示库为空"\n'
        'con = gateway.get_conn()\n'
        'rows = con.execute("SELECT * FROM tool_assets WHERE status=\'active\'").fetchall()\n'
        'txt = [memsearch.asset_text(r) for r in rows]\n'
        'assert all(isinstance(t, str) and t.strip() for t in txt), "资产文本为空"\n'
        'r = gateway.search("检索")\n'
        'print("OK facts=%d assets=%d 资产文本%d条 检索%d条 engine=%s" % (\n'
        '    st["facts"], st["tool_assets"], len(txt), len(r.get("results", [])),\n'
        '    r.get("engine")))\n'
    )
    import subprocess
    env = dict(os.environ)
    env['MEM_DB'] = os.path.abspath(out_path)
    env.setdefault('PYTHONUTF8', '1')
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    py = sys.executable
    p = subprocess.run([py, '-c', code], cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    out = p.stdout.decode('utf-8', 'replace').strip()
    return (p.returncode == 0), out


def selfcheck(out_path):
    """验收口径：不是"跑通了"，而是**逐条验证它真的不含真实串 + 真的能被读**。"""
    con = sqlite3.connect(out_path)
    blob = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for row in con.execute('SELECT * FROM "%s"' % t):
            blob.append(' | '.join('' if x is None else str(x) for x in row))
    con.close()
    text = '\n'.join(blob)
    # 这几类是最该防的（本机路径 / 密钥前缀 / 学号形态 / 真名 / 安全事件词 / 本机账号）
    bad_pat = [
        (r'[A-Za-z]:[\\/]{1,2}Users', 'Windows 用户目录路径'),
        (r'/Users/[A-Za-z]', 'macOS 用户目录路径'),
        (r'sk-[A-Za-z0-9_\-]{6,}', '疑似密钥'),
        (r'\b(19|20)\d{2}\d{6,}\b', '疑似学号/长编号'),
        (r'\bAdministrator\b', '本机账号名'),
    ]
    # ★真名 / 安全事件词来自外置词表（见文件头「敏感词外置」），不写死在源码里
    bad_pat += [(re.escape(x), '真实姓名') for x in _NAMES]
    bad_pat += [(x, '安全事件标记') for x in _INCIDENTS]
    if not (_NAMES or _INCIDENTS):
        print('  ⚠ 降级：本地敏感词表缺失（%s），真名/安全事件两类**未参与检查**' % _TERMS)
    hits = []
    for pat, why in bad_pat:
        m = re.findall(pat, text)
        if m:
            hits.append((why, len(m), sorted(set(m))[:3]))
    par_ok, par_bad = schema_parity(out_path)
    sm_ok, sm_out = smoke(out_path)
    print('=' * 78)
    print('演示库自检：%s' % out_path)
    print('  行数 %d · 文本 %d 字符' % (len(blob), len(text)))
    if hits:
        print('  ★泄密检查发现问题：')
        for why, n, sample in hits:
            print('     %-22s %d 处  %s' % (why, n, sample))
    else:
        print('  ✓ 泄密检查 零命中（无真实路径 / 密钥 / 姓名 / 学号 / 本机账号）')
    if par_ok:
        print('  ✓ 表结构一致性 与 gateway.SCHEMA 完全一致')
    else:
        print('  ★表结构不一致：')
        for b in par_bad:
            print('     %s' % b)
    if sm_ok:
        print('  ✓ 功能冒烟 通过：%s' % sm_out)
    else:
        print('  ★功能冒烟失败：')
        for ln in sm_out.split('\n')[-8:]:
            print('     %s' % ln)
    print('=' * 78)
    return 0 if (not hits and par_ok and sm_ok) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(ROOT, 'demo', 'memory_demo.db'))
    ap.add_argument('--seed', type=int, default=SEED)
    ap.add_argument('--selfcheck', action='store_true', help='只对已存在的库自检')
    a = ap.parse_args()
    if a.selfcheck:
        sys.exit(selfcheck(a.out))
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    counts = build(a.out, a.seed)
    print('已生成 %s' % a.out)
    for k, v in counts.items():
        print('   %-12s %d' % (k, v))
    sys.exit(selfcheck(a.out))


if __name__ == '__main__':
    main()
