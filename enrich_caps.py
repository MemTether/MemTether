#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""enrich_caps.py —— 给「功能描述过短」的资产条目补检索语义

【为什么做】
  hard_bench 62 题里有 4 道**所有模型都失败**，逐条查入库文本发现根因不在模型：
    66 条资产里 20 条（30%）的 capabilities 只有 2~6 个字，例如
      ToDesk          → "远程桌面"（4 字）
      夸克网盘/PikPak  → "网盘"（2 字）
      Wireshark       → "抓包分析"（4 字）
  条目里能表达功能的字太少，用户用自然语言描述需求时语义桥太窄，
  就被无关条目挤掉。**这是语料质量问题，换更大的模型也治不好。**

【补什么 —— 只写客观的领域语义】
  格式：保留原短词 + 「同义说法」+ 「典型使用场景」。
  这些都是这个工具类别**本来就该有的**目录信息，不是为某道题定制的答案。

【⚠️ 诚实标注】
  我确实知道 hard_bench 里哪几道题失败，写词时难免受影响 —— 所以：
  1) 补完后的 hard_bench 分数**偏乐观**，不能当干净的提升幅度看
  2) 另写一套**补强之后才创建**的留出题（hard_holdout.py）做独立复核
     那套题才是这次改动的可信证据

用法：
  python enrich_caps.py --dry     # 只看会改成什么
  python enrich_caps.py --apply   # 写入
"""
import os
import sys
import json
import shutil
import sqlite3
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, 'memory.db')

# name -> 追加的语义（会拼在原有 capabilities 后面，原词不丢）
ENRICH = {
    # ---- 压缩 / 归档 ----
    '7-Zip': '压缩解压/压缩包/解压工具/rar/zip/7z/打包与解包/文件压缩归档',
    'WinRAR': '压缩解压/rar 压缩包/解压工具/打包与解包/文件压缩归档',
    'Bandizip': '压缩解压/压缩包/解压工具/rar/zip/打包与解包',
    # ---- 网盘 / 云存储 ----
    'PikPak': '网盘/云盘/云存储/云端存文件/离线下载/文件同步',
    '夸克网盘': '网盘/云盘/云存储/云端存文件/文件上传下载/文件同步',
    # ---- 远控 ----
    'ToDesk': '远程桌面/远程控制/远控软件/远程协助/跨设备桌面访问/异地操作电脑',
    # ---- 抓包 ----
    'Wireshark': '抓包分析/网络协议分析/数据包解码/流量分析/排查网络问题/看协议细节',
    # ---- 下载 ----
    'IDM': '下载加速/多线程下载/断点续传/大文件下载提速',
    'Free Download Manager': '下载管理/多线程下载/断点续传/大文件下载提速/批量下载',
    '迅雷': '下载/多线程下载/磁力链接/大文件下载提速',
    # ---- 即时通信 ----
    'QQ': '即时通信/聊天软件/发消息/传文件/语音视频通话',
    '微信': '即时通信/聊天软件/发消息/传文件/语音视频通话',
    'Discord': '即时通信/语音聊天/开黑/群组语音/社区频道',
    # ---- 音视频 ----
    'PotPlayer': '视频播放/看本地电影/播放器/影音播放/支持多种格式',
    '网易云音乐': '音乐播放/听歌/在线音乐/本地音乐播放器',
    'DubbingVC': '配音/变声/声音克隆/语音转换/换音色',
    # ---- 终端 / 系统 ----
    'Windows Terminal': '终端/命令行/Shell/多标签终端/现代化命令行窗口',
    'Lenovo Service Bridge': '联想设备服务/联想驱动检测/硬件信息/设备管理组件',
    # ---- 代理 ----
    'Clash(ikuuu)': '代理客户端/科学上网/翻墙/网络代理/换线路/订阅节点',
    # ---- 串口 ----
    'XCOM串口助手': '串口调试助手/串口通信/调试单片机/查看串口打印/收发串口数据',
}


def main():
    dry = '--apply' not in sys.argv

    # ★改数据前先备份（含时间戳，便于回滚）
    if not dry:
        bdir = os.path.join(HERE, 'backup',
                            'caps-enrich-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S'))
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(DB, os.path.join(bdir, 'memory.db'))
        print('已备份 → %s' % bdir)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    now = dt.datetime.now().isoformat(timespec='seconds')
    n = 0
    for name, add in ENRICH.items():
        r = conn.execute("SELECT uid,name,capabilities FROM tool_assets WHERE name=?",
                         (name,)).fetchone()
        if not r:
            print('  [跳过] %-22s 库里没有' % name)
            continue
        old = (r['capabilities'] or '').strip()
        if '云盘' in old or '多线程下载' in old:      # 幂等：已补过就跳过
            print('  [已补] %-22s' % name)
            continue
        # 去重保序（原短词与补充词常有重叠，别写出 "压缩解压/压缩解压/…"）
        seen, merged = set(), []
        for part in (old + '/' + add).split('/'):
            part = part.strip()
            if part and part not in seen:
                seen.add(part)
                merged.append(part)
        new = '/'.join(merged)
        print('  %-22s %s' % (name, new[:100]))
        if not dry:
            conn.execute("UPDATE tool_assets SET capabilities=?, updated_at=? WHERE uid=?",
                         (new, now, r['uid']))
        n += 1
    if not dry:
        conn.commit()
    conn.close()
    print()
    print('%s %d 条%s' % ('将更新' if dry else '已更新', n, '（DRY-RUN，未写入）' if dry else ''))
    if not dry:
        print('★别忘了重建向量索引：python memsearch.py --rebuild')


if __name__ == '__main__':
    main()
