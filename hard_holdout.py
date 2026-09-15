#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hard_holdout.py —— 语料补强的**留出验证集**

【为什么另开一份】
  `hard_bench` 的 62 题在补强**之前**就存在，而我知道其中哪几道失败。
  因此补强后它在那些题上的提升**不能当干净证据**（有调优泄漏）。
  本文件的题目是**补强之后才写的**，措辞与 hard_bench 不重复，
  用来回答一个更关键的问题：
      「补的词能不能泛化到**没用过的问法**上？」
  如果 holdout 也涨 → 补强是真的有效；如果只有 hard_bench 涨 → 就是过拟合。

【复用】直接借用 hard_bench 的查询与判分机器，只替换 CASES。
用法：python hard_holdout.py [model_key]
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hard_bench as HB

# 目标条目与 hard_bench 相同（都是被补强的那 20 条），但**问法完全不同**
HOLDOUT = [
    dict(id='X01', q='从压缩包里把文件取出来要用什么程序？',
         expect=[r'7-Zip|WinRAR|Bandizip|NanaZip|解压|压缩']),
    dict(id='X02', q='下载速度太慢，怎么让它快一点？',
         expect=[r'IDM|Free Download Manager|迅雷|多线程|下载']),
    dict(id='X03', q='想用电脑连上另一台机器操作它的桌面，本机有什么？',
         expect=[r'ToDesk|远程']),
    dict(id='X04', q='网上的协议报文看不懂，有什么能逐层展开看的？',
         expect=[r'Wireshark|协议|抓包|流量']),
    dict(id='X05', q='想把资料存到云上，有哪些客户端可用？',
         expect=[r'网盘|云盘|云存储|PikPak|夸克|Quark|百度']),
    dict(id='X06', q='想听歌，本机装了什么？',
         expect=[r'网易云|CloudMusic|音乐']),
    dict(id='X07', q='看电影的软件叫什么？',
         expect=[r'PotPlayer|播放']),
    dict(id='X08', q='想把录音里的声音换成另一个人的音色',
         expect=[r'DubbingVC|变声|克隆|配音']),
    dict(id='X09', q='命令行窗口能不能开多个标签页？',
         expect=[r'Terminal|终端']),
    dict(id='X10', q='联想笔记本的硬件检测组件在哪？',
         expect=[r'Lenovo|联想']),
    dict(id='X11', q='怎么让流量走别的线路出去？',
         expect=[r'Clash|ikuuu|代理|vpn|VPN']),
    dict(id='X12', q='单片机串口收到的数据看不到，用什么看？',
         expect=[r'XCOM|串口']),
    dict(id='X13', q='跟队友连麦打游戏装了什么？',
         expect=[r'Discord|语音']),
    dict(id='X14', q='压缩软件除了常用的那个还有别的吗？',
         expect=[r'7-Zip|WinRAR|Bandizip|NanaZip|压缩|解压']),
    dict(id='X15', q='大文件下载老是中断，有什么能接着下的？',
         expect=[r'IDM|Free Download Manager|迅雷|续传|下载']),
    dict(id='X16', q='手机上聊天，电脑上也想同步消息，装了什么？',
         expect=[r'微信|Weixin|QQ|即时通信|聊天']),
    dict(id='X17', q='本地存的音乐文件用什么放？',
         expect=[r'网易云|CloudMusic|音乐|播放']),
    dict(id='X18', q='想远程帮家里人处理电脑问题，用什么？',
         expect=[r'ToDesk|远程']),
    # 反向对照：**没有**被补强的条目，用来确认改动没把别的东西挤坏
    dict(id='X19', q='写单片机的程序用哪个 IDE？',
         expect=[r'STM32CubeIDE|Arduino|IDE']),
    dict(id='X20', q='怎么彻底卸载软件并清掉残留？',
         expect=[r'GeekUninstaller|卸载|清理']),
    dict(id='X21', q='想本地跑 AI 画图，有什么界面？',
         expect=[r'ComfyUI|Fooocus|SwarmUI|AI绘图|生图']),
    dict(id='X22', q='解压之外，能不能修复坏掉的压缩包？',
         expect=[r'Object Fix Zip|修复|压缩']),
]


def main():
    HB.CASES = HOLDOUT
    HB.RESULT = os.path.join(HERE, 'hard_holdout_result.json')
    mk = sys.argv[1] if len(sys.argv) > 1 else 'bge-m3-int8'
    data, err = HB._query_all(mk)
    if data is None:
        print('查询失败:', err)
        return
    res = HB.score(data['rows'])
    npass = sum(1 for _, s, _ in res if s == 'PASS')
    print('=' * 72)
    print('语料补强 · 留出验证集（%d 题，补强后才创建）' % len(res))
    print('后端 %s' % data['info'])
    print('=' * 72)
    print('  得分 %d/%d = %.1f%%' % (npass, len(res), npass / len(res) * 100))
    print('-' * 72)
    for cid, st, sim in res:
        q = next(c['q'] for c in HOLDOUT if c['id'] == cid)
        mark = 'PASS' if st == 'PASS' else 'FAIL'
        print('  [%s] %-4s %s' % (mark, cid, q))
    print('=' * 72)


if __name__ == '__main__':
    main()
