#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hard_bench.py —— 语义检索难题集（改述式查询）

【为什么还要另建一份卷子】
  现有 `asset_bench`（23 题）在**纯关键词兜底**状态下就已经 23/23 = 100%。
  它测不出 embedding 的好坏 —— 因为题目都形如「本机 X 装在哪里」，
  而记忆条目里就写着 X 的名字，关键词直接命中。
  **饱和的卷子没有鉴别力。**（这是 2026-09-15 实测发现的，不是推测。）

  本卷子的设计约束：**提问里不出现目标工具的（英文）名称**，
  只描述功能、场景、俗称。逼检索真的靠语义，而不是字符串匹配。
      例：不问「LibreOffice 在哪」，而问
          「我要把 Word 文档导出成 PDF 交作业，本机有什么现成工具？」

【判分】expect 命中任一正则 且 forbid 全部不命中 → PASS。
  答案键都是**本机实测事实**（路径存在、资产确实在库），不依赖 LLM judge。

【用法】
  python hard_bench.py selftest            # 题集传参自检（秒级，不加载模型）
  python hard_bench.py run                 # 当前后端跑一次
  python hard_bench.py compare             # 逐个模型：重建索引 → 跑 → 出对比表
"""
import os
import re
import sys
import json
import time
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from interpreter import resolve_python, require_modules      # noqa: E402

# ★2026-09-18 改：不再写死 `.venv-memory` —— 发布库（clone 出来的）里没有这个目录，
#   写死等于「README 说能复跑、实际一跑就 WinError 2」。统一走 interpreter 解析。
PY, PY_SRC = resolve_python(HERE, announce=True)
# 评测必须真跑语义路；缺 chromadb/numpy 会静默降级成纯关键词 → 分数不可信 → 直接拦。
NEED_MODULES = ('chromadb', 'numpy')


def cases_ipc(cases=None):
    """把题集写成一个「子进程读得到」的临时文件，返回其路径。

    ★为什么必须走系统临时目录，而不是仓库里的 _hb_cases.json（2026-09-16 修）
      那个文件从来只是「父进程写 → 子进程读」的进程间传参通道，
      却被当成仓库文件提交了。而 hard_holdout.py 会执行 `HB.CASES = HOLDOUT`
      把本题集的 CASES 换成 22 题留出集；于是
          「先跑留出集 → 再跑任何 import hard_bench 的脚本」
      就会把 22 题**写回仓库里的 62 题文件**，题集真源被静默污染
      （rerank_k_bench 因此只跑 22 题，看起来"跑通了"其实卷子被换了）。

      题集真源只有一个：本模块的 CASES（带答案键，可判分）。
      传参改走临时文件后，仓库不再参与，这类污染从结构上不可能发生。
    """
    fd, path = tempfile.mkstemp(suffix='.json', prefix='hb_cases_')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump([{'id': c['id'], 'q': c['q']} for c in (cases or CASES)],
                  f, ensure_ascii=False)
    return path

# id, 问题（回避实体名）, 期望命中正则（任一）, 禁止出现正则
CASES = [
    # ---- 文档 / 办公 ----
    dict(id='H01', q='我要把 Word 文档导出成 PDF 交作业，本机有什么现成工具？',
         expect=[r'LibreOffice|soffice']),
    dict(id='H02', q='写论文时想批量排版，有没有能读写 docx/xlsx/pptx 的免费办公套件？',
         expect=[r'LibreOffice|soffice']),

    # ---- 文件操作 ----
    dict(id='H03', q='哪个程序能在几秒内搜遍整块硬盘找出某个文件名？',
         expect=[r'Everything']),
    dict(id='H04', q='我要解压一个 rar 压缩包，本机有哪些压缩工具？',
         expect=[r'7-Zip|Bandizip|NanaZip|Object Fix Zip|WinRAR|7z\.exe']),
    dict(id='H05', q='压缩包打不开了，有没有能修复损坏压缩文件的工具？',
         expect=[r'Object Fix Zip|修复|banzai|Zip']),
    dict(id='H06', q='硬盘里重复文件太多，有什么工具能找出来删掉？',
         expect=[r'Czkawka|重复']),

    # ---- 网络 / 远程 ----
    dict(id='H07', q='我那个科学上网的客户端装在哪个目录？',
         expect=[r'Clash|ikuuu']),
    dict(id='H08', q='想远程控制另一台电脑，本机装了什么远控软件？',
         expect=[r'ToDesk']),
    dict(id='H09', q='我想抓取网卡上的数据包做协议分析，要装什么驱动库？',
         expect=[r'Npcap|WinPcap|pcap']),
    dict(id='H10', q='下载大文件想提速，有什么多线程下载工具？',
         expect=[r'IDM|Free Download Manager|FDM|迅雷']),

    # ---- 存储 / 网盘 ----
    dict(id='H11', q='云端存文件的客户端程序装在哪？',
         expect=[r'百度网盘|BDN|BaiduNetdisk|PikPak']),

    # ---- 嵌入式 / 硬件 ----
    dict(id='H12', q='我要写单片机的 C 程序，用哪个 IDE？装在哪？',
         expect=[r'STM32CubeIDE']),
    dict(id='H13', q='把编译好的固件烧进单片机芯片要用什么工具？',
         expect=[r'STM32CubeProgrammer|烧录|烧写|ST-LINK|stm32flash']),
    dict(id='H14', q='调试单片机时想看串口打印的信息，用什么软件？',
         expect=[r'XCOM|串口']),
    dict(id='H15', q='USB 转串口的小板子插上没反应，要装什么驱动？',
         expect=[r'CH341|CH34|串口驱动']),
    dict(id='H16', q='玩开源硬件开发板，写代码和编译用什么环境？',
         expect=[r'Arduino|STM32|CubeIDE']),

    # ---- 多媒体 ----
    dict(id='H17', q='看本地下载的电影用什么播放器？装在哪？',
         expect=[r'PotPlayer|播放器']),
    dict(id='H18', q='想用 AI 在本地画图，有什么界面可以用？',
         expect=[r'ComfyUI|Fooocus|SwarmUI|Stable Diffusion']),
    dict(id='H19', q='想把一段录音换成别人的声音，本机有变声/克隆语音的工具吗？',
         expect=[r'DubbingVC|变声|克隆']),

    # ---- 系统 / 维护 ----
    dict(id='H20', q='彻底卸载软件并且清掉注册表残留，用什么工具？',
         expect=[r'GeekUninstaller|Geek']),
    dict(id='H21', q='重装系统后驱动都没了，有什么一键装驱动的工具？',
         expect=[r'DrvCeo|驱动总裁|驱动']),
    dict(id='H22', q='想修改单机游戏的数值，有什么内存修改工具？',
         expect=[r'Cheat Engine']),
    dict(id='H23', q='安卓程序跑不起来想试试模拟器，本机装了吗？',
         expect=[r'MuMu|模拟器']),

    # ---- 开发 / 自动化 ----
    dict(id='H24', q='我平时写代码用的编辑器和终端类是哪个？',
         expect=[r'VS Code|Code\.exe|Cursor|Codex']),
    dict(id='H25', q='想让脚本自动点鼠标、填表单，本机有什么自动化工具或脚本？',
         expect=[r'my_gui|自动化|pyautogui|Python']),
    dict(id='H26', q='想用命令行让 AI 帮我改代码，装了什么工具？',
         expect=[r'Codex|OpenClaw|CLI']),
    dict(id='H27', q='有没有能直接分析安卓安装包、看它连了哪些服务器的工具？',
         expect=[r'APK|apk-tools|apk_server_check']),

    # ---- 网络浏览 ----
    dict(id='H28', q='本机默认用什么浏览器？',
         expect=[r'Edge|msedge|Chrome|浏览器']),
    dict(id='H29', q='想跟人语音聊天开黑，装了什么聊天软件？',
         expect=[r'Discord|QQ|微信|Weixin']),

    # ---- 中枢自知（跨域）----
    dict(id='H30', q='我怎么才能知道自己的记忆库打分是多少？',
         expect=[r'hub_score|评分卡|打分']),

    # ================= 扩充段（H31+）：为降低抽样误差，题量翻倍 =================
    # 动机：30 题时 76.7% vs 83.3% 只差 2 题，95% 置信区间约 ±14pp，
    #       **统计上无法区分**。要把模型差异测得可信，必须加题。

    # ---- 网络 / 抓包 ----
    dict(id='H31', q='想分析网络数据包、逐层看协议细节，装了什么软件？',
         expect=[r'Wireshark|wireshark']),
    dict(id='H32', q='想抓本机流量做安全排查，本机有几种抓包方案？',
         expect=[r'Npcap|WinPcap|Wireshark|pcap']),
    dict(id='H33', q='连不上公司内网，有没有能换线路的客户端？',
         expect=[r'ikuuu|Clash|vpn|VPN']),

    # ---- 下载 / 媒体 ----
    dict(id='H34', q='想把视频网站上的片子存到本地，有工具吗？',
         expect=[r'YTDownloader|yt-dlp|下载']),
    dict(id='H35', q='听歌用什么软件？装在哪？',
         expect=[r'CloudMusic|网易云|音乐']),
    dict(id='H36', q='想存一些大文件到云端，除了常见那个还有别的网盘吗？',
         expect=[r'PikPak|夸克|Quark|百度网盘|BDN']),

    # ---- 开发工具 ----
    dict(id='H37', q='有没有比传统命令行更好用的终端？',
         expect=[r'Terminal|终端']),
    dict(id='H38', q='想用一条命令查本机装了哪些软件、或者装新软件？',
         expect=[r'winget|包管理']),
    dict(id='H39', q='想跑一段自动化脚本，本机装了解释器吗？',
         expect=[r'Python|Node|解释器']),
    dict(id='H40', q='用数据线连安卓手机、装安装包看日志，要什么工具？',
         expect=[r'adb|platform-tools']),

    # ---- 嵌入式 / 硬件（补）----
    dict(id='H41', q='自己焊的国产芯片开发板怎么烧程序进去？',
         expect=[r'WCHISPTool|WCH|ISP']),
    dict(id='H42', q='eNSP 里那些路由器其实是靠什么跑起来的？引擎在哪？',
         expect=[r'VBoxManage|VirtualBox|虚拟机']),
    dict(id='H43', q='华为网络设备仿真软件装在哪？',
         expect=[r'eNSP|AXUEXI']),
    dict(id='H44', q='给 Arduino 开发板写程序用哪个软件？',
         expect=[r'Arduino']),
    dict(id='H45', q='单片机除了在线调试，能不能用串口直接烧程序？',
         expect=[r'stm32_alarm|ISP|串口烧录|STM32工具链']),

    # ---- AI / 客户端 ----
    dict(id='H46', q='能同时接好几个大模型的桌面聊天客户端，本机有吗？',
         expect=[r'Chatbox']),
    dict(id='H47', q='国内那个字节出的 AI 助手客户端装在哪？',
         expect=[r'Doubao|豆包']),
    dict(id='H48', q='想用网页界面一键出图，不用自己配环境，有什么？',
         expect=[r'SwarmUI|ComfyUI|Fooocus|生图']),
    dict(id='H49', q='本机有没有能被别的程序指挥干活的 agent 网关？',
         expect=[r'OpenClaw|agentctl']),

    # ---- 安全 / 凭据 ----
    dict(id='H50', q='本机的各种密钥、密码加密存在什么地方？',
         expect=[r'vault|凭据|DPAPI|secure']),
    dict(id='H51', q='打游戏时后台运行、防作弊的那个系统服务是什么？',
         expect=[r'AntiCheat|反作弊|ACE']),
    dict(id='H52', q='有没有清理系统垃圾、把顽固文件彻底删掉的工具？',
         expect=[r'GeekUninstaller|清理|卸载']),
    dict(id='H53', q='CF 游戏的辅助脚本工具？',
         expect=[r'CF活动助手|cfzhushou|CF']),

    # ---- 交易 / 财经 ----
    dict(id='H54', q='看期货行情下单用什么软件？',
         expect=[r'同花顺|期货']),

    # ---- 中枢自知（补）----
    dict(id='H55', q='AI 的「技能」是以什么格式、存在哪个目录？',
         expect=[r'skill|技能|SKILL\.md']),
    dict(id='H56', q='我上次让 AI 记住的事情，它存在哪张表里？',
         expect=[r'facts|tool_assets|memory\.db|记忆']),
    dict(id='H57', q='怎么让另一个 AI 客户端也读到同一份记忆？',
         expect=[r'MCP|投影|槽位|同步|互通']),

    # ---- 硬件 / 外设 ----
    dict(id='H58', q='我的机械键盘想改灯效，装了什么配套软件？',
         expect=[r'REDRAGON|G62|键鼠驱动']),
    dict(id='H59', q='联想笔记本自带的那些驱动和检测组件在哪？',
         expect=[r'Lenovo|联想']),
    dict(id='H60', q='想装虚拟机跑别的系统，本机有虚拟化软件吗？',
         expect=[r'VirtualBox|VBoxManage|虚拟机|MuMu']),

    # ---- 文档 / 浏览器（补）----
    dict(id='H61', q='想把网页另存成 PDF，能不能不开界面就做到？',
         expect=[r'Edge|msedge|headless|无头']),
    dict(id='H62', q='想批量把表格数据转成 PDF，用什么？',
         expect=[r'LibreOffice|soffice|Excel']),
]

RESULT = os.path.join(HERE, 'hard_bench_result.json')


def _reload_memsearch(model_key):
    """在新进程里用指定模型重建索引（避免同进程内 ONNX 会话串味）。"""
    env = dict(os.environ)
    env['MEM_EMBED_MODEL'] = model_key
    env['MEM_EMBED_BACKEND'] = 'local'
    env['PYTHONPATH'] = HERE
    r = subprocess.run([PY, 'memsearch.py', '--rebuild'], cwd=HERE, env=env,
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
    tail = (r.stdout or '').strip().splitlines()[-2:]
    return r.returncode == 0, ' / '.join(tail)


def _query_all(model_key, limit=10):
    """在独立进程里跑全部查询（同一进程内模型只加载一次，快很多）。

    limit 取 10 = search_hybrid 的生产默认值，别自己发明不同的 top-k，
    否则测的就不是生产行为。
    """
    # ★2026-09-18：这里是**唯一**真加载模型的路径，也是**唯一**能兜住「外部 import 调用方」
    #   的位置 —— hard_holdout.py / qvalue_ab.py 都是 `import hard_bench` 之后直接调
    #   `_query_all()`，**不会**经过本文件的 `__main__` 块，所以入口那层拦截对它们无效。
    #   实测（发布库，用缺 chromadb 的系统 3.10 跑 qvalue_ab.py）：
    #     PY 存在 → 不报 WinError 2 → 子进程真跑起来 → 静默降级成纯关键词
    #     → 出一份**看着正常、其实测的是别的东西**的分数。
    #   缺依赖必须在这里就停。入口那层拦截仍然保留，作用不同：
    #   它让 compare 在**重建索引之前**就失败（否则要等第一个模型 rebuild 完才报错）。
    require_modules(PY, *NEED_MODULES)
    env = dict(os.environ)
    env['MEM_EMBED_MODEL'] = model_key
    env['MEM_EMBED_BACKEND'] = 'local'
    env['PYTHONPATH'] = HERE
    env['HB_CASES'] = cases_ipc()
    script = r'''
import os, sys, json
sys.path.insert(0, %r)
import memsearch
cases = json.load(open(os.environ['HB_CASES'], encoding='utf-8'))
out = []
for c in cases:
    try:
        r = memsearch.search_hybrid(c['q'], limit=%d, decay=False)
        blob = [x['content'] for x in r.get('results', [])]
        sims = [x.get('semantic', 0) for x in r.get('results', [])]
    except Exception as e:
        blob = ['[ERR] %%s' %% e]; sims = []
    out.append({'id': c['id'], 'blob': blob, 'sim_max': max(sims) if sims else 0.0})
print(json.dumps({'info': memsearch.LAST_EMBED_INFO, 'rows': out}, ensure_ascii=False))
''' % (HERE, limit)
    try:
        r = subprocess.run([PY, '-c', script], cwd=HERE, env=env,
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3600)
    finally:
        try:
            os.remove(env['HB_CASES'])
        except OSError:
            pass
    if r.returncode != 0:
        return None, (r.stderr or '')[-300:]
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]), None
    except Exception as e:
        return None, 'parse: %s / %s' % (e, (r.stdout or '')[-200:])


def score(rows):
    m = {x['id']: x for x in rows}
    res = []
    for c in CASES:
        x = m.get(c['id'])
        if not x:
            res.append((c['id'], 'MISS', 0.0)); continue
        text = ' \n '.join(x['blob'])
        hit = any(re.search(p, text, re.I) for p in c['expect'])
        res.append((c['id'], 'PASS' if hit else 'FAIL', x.get('sim_max', 0.0)))
    return res


def compare(models=None):
    models = models or ['bge-small-zh', 'bge-base-zh', 'bge-m3-int8', 'bge-m3']
    print('=' * 84)
    print('语义检索难题集 hard_bench —— 逐模型对比（每题重建索引，走生产链路）')
    print('=' * 84)
    table = {}
    for mk in models:
        t0 = time.time()
        ok, tail = _reload_memsearch(mk)
        if not ok:
            print('  [%s] 重建索引失败: %s' % (mk, tail)); continue
        data, err = _query_all(mk)
        if data is None:
            print('  [%s] 查询失败: %s' % (mk, err)); continue
        res = score(data['rows'])
        npass = sum(1 for _, s, _ in res if s == 'PASS')
        table[mk] = dict(pass_n=npass, total=len(res), res=res,
                         dim=data['info'].get('dim'),
                         secs=round(time.time() - t0, 1))
        print('  [%-14s] %2d/%2d = %5.1f%%   dim=%-5s  用时 %ss'
              % (mk, npass, len(res), npass / len(res) * 100,
                 data['info'].get('dim'), table[mk]['secs']))

    # 逐题对比矩阵
    keys = [k for k in table]
    if keys:
        print()
        print('  逐题（O=命中 / .=未命中）')
        print('  %-6s %s' % ('题号', ' '.join('%-13s' % k for k in keys)))
        for i, c in enumerate(CASES):
            marks = []
            for k in keys:
                marks.append('%-13s' % ('O' if table[k]['res'][i][1] == 'PASS' else '.'))
            print('  %-6s %s' % (c['id'], ' '.join(marks)))
        print()
        print('  未命中清单（首个模型为准）')
        for i, c in enumerate(CASES):
            bad = [k for k in keys if table[k]['res'][i][1] != 'PASS']
            if bad:
                print('    %s [%s] %s' % (c['id'], ','.join(bad), c['q']))

    with open(RESULT, 'w', encoding='utf-8') as f:
        json.dump({k: {kk: vv for kk, vv in v.items() if kk != 'res'} for k, v in table.items()},
                  f, ensure_ascii=False, indent=2)
    print('\n结果已存 %s' % RESULT)
    return table


def run(model_key=None):
    model_key = model_key or os.environ.get('MEM_EMBED_MODEL') or 'bge-m3'
    data, err = _query_all(model_key)
    if data is None:
        print('查询失败:', err); return
    res = score(data['rows'])
    npass = sum(1 for _, s, _ in res if s == 'PASS')
    print('模型 %s（%s）' % (model_key, data['info']))
    print('  得分 %d/%d = %.1f%%' % (npass, len(res), npass / len(res) * 100))
    for cid, st, sim in res:
        if st != 'PASS':
            q = next(c['q'] for c in CASES if c['id'] == cid)
            print('    [FAIL] %s  %s' % (cid, q))


def selftest():
    """快速自检：题集传参通道不得写回仓库（2026-09-16 加入的回归保护）。

    背景见 cases_ipc 的文档串。断言三件事：
      ① 传参文件落在系统临时目录，不在仓库目录；
      ② 跑一趟之后仓库目录不多出任何文件（尤其不出现 _hb_cases.json）；
      ③ 传参内容跟随传入的题集（这是 hard_holdout 换卷子能生效的前提）。
    不加载任何模型，秒级完成，可以放进回归流水线。
    """
    fails = []
    before = set(os.listdir(HERE))
    p1 = cases_ipc()
    n1 = len(json.load(open(p1, encoding='utf-8')))
    if os.path.dirname(os.path.abspath(p1)) == HERE:
        fails.append('传参文件落在了仓库目录：%s' % p1)
    if n1 != len(CASES):
        fails.append('传参题数 %d != 当前 CASES %d' % (n1, len(CASES)))

    probe = [dict(id='Z1', q='探针一'), dict(id='Z2', q='探针二')]
    p2 = cases_ipc(probe)
    n2 = len(json.load(open(p2, encoding='utf-8')))
    if n2 != 2:
        fails.append('显式传入题集未生效：期望 2 题，实得 %d' % n2)

    for p in (p1, p2):
        try:
            os.remove(p)
        except OSError:
            pass

    leftover = sorted(set(os.listdir(HERE)) - before)
    if leftover:
        fails.append('仓库目录多出文件：%s' % leftover)
    if os.path.exists(os.path.join(HERE, '_hb_cases.json')):
        fails.append('仓库里又出现 _hb_cases.json —— 传参通道被写回仓库了')

    if fails:
        print('题集传参自检：FAIL')
        for x in fails:
            print('  - %s' % x)
        return 1
    print('题集传参自检：PASS（临时目录 %s；当前卷子 %d 题；仓库目录无新增文件）'
          % (os.path.dirname(p1), len(CASES)))
    return 0


if __name__ == '__main__':
    a = sys.argv[1] if len(sys.argv) > 1 else 'run'
    # ★2026-09-18：selftest 是「传参通道」自检（临时文件 + 仓库目录），毫秒级、不加载模型，
    #   所以不拦依赖 —— 缺 chromadb 的机器也该能跑它来排障。
    #   其余分支要真出分数，缺依赖会静默降级成纯关键词 → 分数不可信 → 先拦死。
    if a != 'selftest':
        require_modules(PY, *NEED_MODULES)
    if a == 'compare':
        models = sys.argv[2:] if len(sys.argv) > 2 else None
        compare(models)
    elif a == 'selftest':
        sys.exit(selftest())
    else:
        run(sys.argv[2] if len(sys.argv) > 2 else None)
