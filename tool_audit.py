#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tool_audit.py —— 本机工具资产普查 + 校验（可重复运行）

定位：解决"记忆里压根没这条"和"记错了没人发现"两个病。
- audit()  : 扫盘，产出候选资产清单（不写库）
- verify() : 对 tool_assets 每条跑校验，失活即标记 status
- seed()   : 把审计结果写入 tool_assets（upsert，带 verification_method）

用法：
  python tool_audit.py audit     # 只扫，打印
  python tool_audit.py verify    # 校验库内条目
  python tool_audit.py seed      # 写入/更新库
"""
import os
import sys
import json
import sqlite3
import subprocess
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "memory.db")

# ---------------------------------------------------------------- 资产目录
    # 每条：(name, path, kind, entrypoint, verify_cmd, capabilities, notes)
# verify_cmd 是 shell 命令，exit 0 = 存活。全部用 test -f / -d，不依赖外部程序。
# ★原则：entrypoint 必须是实测验证过的，且 seed 每次全字段覆盖，防止旧错误残留。
ASSETS = [
    # ===== 网络仿真 / 实验 =====
    ("eNSP", r"E:\AXUEXI\eNSP\eNSP_Client.exe", "local_tool",
     "运行 eNSP_Client.exe；设备由 VBoxHeadless 托管 VM；telnet 127.0.0.1:2000/2001/2002",
     r'test -f "E:/AXUEXI/eNSP/eNSP_Client.exe"',
     "华为网络设备仿真,拓扑搭建,路由器/交换机/PC模拟",
     "★2026-09-15 修正：真实路径 E:\\AXUEXI\\eNSP\\，不是 E:\\RUANJIAN。"
     "telnet 控制台端口 2000/2001/2002（对应 AR1/AR2/AR3），不是 2010/2011/2012。"
     "★telnet 只返回 # 提示符、不回显命令输出，回显走 GUI，必须 GUI 抓图。"),
    ("VirtualBox(eNSP内置)", r"E:\AXUEXI\VBoxManage.exe", "local_tool",
     "VirtualBox(eNSP内置)：见 path",
     r'test -f "E:/AXUEXI/VBoxManage.exe"',
     "虚拟机管理,托管eNSP设备VM(AR_Clone_<设备ID>)",
     "eNSP 设备实为 VBoxHeadless 托管的 VM。 .topo 是 XML，含设备 id/name/cx/cy/com_port。"),
    ("Wireshark", r"E:\AXUEXI\Wireshark_std_setup.exe", "installer",
     "Wireshark：见 path",
     r'test -f "E:/AXUEXI/Wireshark_std_setup.exe"',
     "抓包分析",
     ""),
    # ===== 文档 / 转换 =====
    ("LibreOffice", r"E:\RUANJIAN\LibreOffice\program\soffice.exe", "local_tool",
     "LibreOffice：见 path",
     r'test -f "E:/RUANJIAN/LibreOffice/program/soffice.exe"',
     "docx→pdf 无头转换,Excel/PPT/Word 读写",
     "★2026-09-15 补录：此前库内完全缺失，导致我误判「没装Office」。"
     "用法：soffice.exe --headless --convert-to pdf --outdir <dir> <file>，"
     "一条命令即可，页眉页码全对。不要再用 HTML+Edge 打印那套。"),
    # ===== AI / 生图 =====
    ("ComfyUI", r"E:\ComfyUI\ComfyUI_windows_portable", "local_tool",
     "ComfyUI：见 path",
     r'test -d "E:/ComfyUI/ComfyUI_windows_portable"',
     "AI绘图,文生图,图生图",
     "torch2.13.0+cu130 可用。入口 run_nvidia_gpu.bat，端口8188。"),
    ("Fooocus", r"E:\RUANJIAN\Fooocus\Fooocus_win64_2-5-0", "local_tool",
     "Fooocus：见 path",
     r'test -d "E:/RUANJIAN/Fooocus/Fooocus_win64_2-5-0"',
     "AI绘图,文生图(一键)",
     "内嵌 torch2.1.0+cu121 与驱动 CUDA13.2 不兼容会崩，已改用 ComfyUI 替代。"),
    ("SwarmUI", r"E:\SwarmUI", "local_tool",
     "SwarmUI：见 path",
     r'test -d "E:/SwarmUI"',
     "ComfyUI 的傻瓜前门,一键生图 WebUI",
     ""),
    # ===== 安卓 / 移动 =====
    ("MuMu模拟器", r"E:\RUANJIAN\MuMuPlayer\nx_main\MuMuManager.exe", "local_tool",
     "MuMu模拟器：见 path",
     r'test -f "E:/RUANJIAN/MuMuPlayer/nx_main/MuMuManager.exe"',
     "安卓模拟,x86_64(跑不了armeabi-v7a)",
     "实例会反复自行退出，别把结论建立在模拟器上。"),
    ("platform-tools(adb)", r"E:\RUANJIAN\platform-tools", "local_tool",
     "platform-tools(adb)：见 path",
     r'test -d "E:/RUANJIAN/platform-tools"',
     "adb,安卓设备调试",
     ""),
    # ===== 嵌入式 / 硬件 =====
    ("STM32工具链", r"E:\RUANJIAN\stm32_alarm", "local_tool",
     "STM32工具链：见 path",
     r'test -d "E:/RUANJIAN/stm32_alarm"',
     "STM32烧录(ISP),串口助手",
     "STM32_Programmer_CLI -c port=COM3 br=115200。蜂鸣器 GPIO 定位不到，疑经三极管驱动。"),
    ("STM32CubeIDE", r"E:\QRS\STM32CubeIDE_1.16.0", "local_tool",
     "STM32CubeIDE：见 path",
     r'test -d "E:/QRS/STM32CubeIDE_1.16.0"',
     "STM32 IDE,工程编译,烧录",
     "★路径在 E:\\QRS\\，不在 E:\\RUANJIAN\\。工作区 E:\\QRS\\STM32_Workspace。"),
    ("STM32CubeProgrammer", r"E:\QRS\SetupSTM32CubeProgrammer_win64.exe", "installer",
     "STM32CubeProgrammer：见 path",
     r'test -f "E:/QRS/SetupSTM32CubeProgrammer_win64.exe"',
     "STM32 下载/烧录工具",
     ""),
    ("XCOM串口助手", r"E:\QRS\XCOM V2.6.exe", "local_tool",
     "XCOM串口助手：见 path",
     r'test -f "E:/QRS/XCOM V2.6.exe"',
     "串口调试助手",
     "★路径在 E:\\QRS\\。"),
    ("CH341驱动", r"E:\QRS\CH341SER.EXE", "driver",
     "CH341驱动：见 path",
     r'test -f "E:/QRS/CH341SER.EXE"',
     "USB转串口驱动",
     ""),
    ("Arduino IDE", r"E:\RUANJIAN\Arduino IDE", "local_tool",
     "Arduino IDE：见 path",
     r'test -d "E:/RUANJIAN/Arduino IDE"',
     "Arduino 开发",
     ""),
    # ===== 安全 / 逆向 =====
    ("APK研判工具", r"E:\RUANJIAN\apk-tools\apk_server_check.py", "local_tool",
     "APK研判工具：见 path",
     r'test -f "E:/RUANJIAN/apk-tools/apk_server_check.py"',
     "APK完整性检查,挖域名,三级DNS探活",
     "全 Python 标准库实现，本机无 Java/adb/apktool 也能跑，别装 JDK。"),
    ("凭据管理vault", r"E:\RUANJIAN\.secure\vault\vault.bin", "local_tool",
     "凭据管理vault：见 path",
     r'test -f "E:/RUANJIAN/.secure/vault/vault.bin"',
     "DPAPI加密凭据,统一出口",
     "ai-audit\\cred_env.py env() 灌环境变量。旧问题：明文key曾散落62文件。"),
    # ===== 系统 / 效率 =====
    ("Everything", r"E:\RUANJIAN\Everything\Everything.exe", "local_tool",
     "Everything：见 path",
     r'test -f "E:/RUANJIAN/Everything/Everything.exe"',
     "秒级全盘文件搜索",
     "★2026-09-15 补录。找文件优先用它，别用逐目录遍历。"),
    ("Czkawka", r"E:\RUANJIAN\Czkawka", "local_tool",
     "Czkawka：见 path",
     r'test -d "E:/RUANJIAN/Czkawka"',
     "重复文件查找,大文件清理",
     ""),
    ("GeekUninstaller", r"E:\RUANJIAN\GeekUninstaller.exe", "local_tool",
     "GeekUninstaller：见 path",
     r'test -f "E:/RUANJIAN/GeekUninstaller.exe"',
     "软件卸载,残留清理",
     "目录下有 HD_GeekUninstaller.exe，是同名兄弟（第三方包装器特征，勿用）。"),
    ("7-Zip", r"C:\Program Files\7-Zip\7z.exe", "local_tool",
     "7-Zip：见 path",
     r'test -f "C:/Program Files/7-Zip/7z.exe"',
     "压缩解压",
     "★在 C 盘，不在 E 盘。"),
    ("WinRAR", r"C:\Program Files\WinRAR\WinRAR.exe", "local_tool",
     "WinRAR：见 path",
     r'test -f "C:/Program Files/WinRAR/WinRAR.exe"',
     "压缩解压",
     "目录下有 HD_WinRAR.exe（第三方包装器特征，勿用）。"),
    # ===== 媒体 =====
    ("PotPlayer", r"E:\RUANJIAN\PotPlayer\PotPlayerMini64.exe", "local_tool",
     "PotPlayer：见 path",
     r'test -f "E:/RUANJIAN/PotPlayer/PotPlayerMini64.exe"',
     "视频播放",
     ""),
    ("Free Download Manager", r"E:\RUANJIAN\Free Download Manager", "local_tool",
     "Free Download Manager：见 path",
     r'test -d "E:/RUANJIAN/Free Download Manager"',
     "下载管理",
     ""),
    ("IDM", r"E:\RUANJIAN\IDM", "local_tool",
     "IDM：见 path",
     r'test -d "E:/RUANJIAN/IDM"',
     "下载加速",
     ""),
    ("DubbingVC", r"E:\RUANJIAN\DubbingVC", "local_tool",
     "DubbingVC：见 path",
     r'test -d "E:/RUANJIAN/DubbingVC"',
     "配音/变声",
     ""),
    # ===== 开发环境 =====
    ("Node.js(系统)", r"C:\Program Files\nodejs\node.exe", "runtime",
     "Node.js(系统)：见 path",
     r'test -f "C:/Program Files/nodejs/node.exe"',
     "Node 运行时",
     "另有 WorkBuddy 托管版 22.22.2-3，优先用托管的。"),
    ("Codex CLI", r"E:\RUANJIAN\Codex", "local_tool",
     "Codex CLI：见 path",
     r'test -d "E:/RUANJIAN/Codex"',
     "OpenAI Codex 命令行 agent",
     ""),
    ("OpenClaw", r"E:\RUANJIAN\OpenClaw", "local_tool",
     "OpenClaw：见 path",
     r'test -d "E:/RUANJIAN/OpenClaw"',
     "本地 agent 网关(可被 agentctl 指挥)",
     "见技能 local-agent-relay-command。"),
    # ===== 协作 / 通信 =====
    ("Chatbox", r"E:\RUANJIAN\Chatbox", "local_tool",
     "Chatbox：见 path",
     r'test -d "E:/RUANJIAN/Chatbox"',
     "多模型聊天客户端",
     ""),
    ("Discord", r"E:\RUANJIAN\Discord", "local_tool",
     "Discord：见 path",
     r'test -d "E:/RUANJIAN/Discord"',
     "即时通信",
     ""),
    ("QQ", r"E:\RUANJIAN\QQ.exe", "local_tool",
     "QQ：见 path",
     r'test -f "E:/RUANJIAN/QQ.exe"',
     "即时通信",
     "★在 E:\\RUANJIAN 根目录，不在 Program Files。"),
    ("ToDesk", r"C:\ToDesk\ToDesk.exe", "local_tool",
     "ToDesk：见 path",
     r'test -f "C:/ToDesk/ToDesk.exe"',
     "远程桌面",
     "★在 C:\\ToDesk\\。"),
    # ===== 磁盘 / 网盘 =====
    ("百度网盘", r"E:\BDN_extract_test\BaiduNetdisk.exe", "local_tool",
     "百度网盘：见 path",
     r'test -f "E:/BDN_extract_test/BaiduNetdisk.exe"',
     "网盘同步/下载",
     "★解包目录 E:\\BDN_extract_test\\。"),
    ("夸克网盘", r"E:\RUANJIAN\QuarkCloudDrive", "local_tool",
     "夸克网盘：见 path",
     r'test -d "E:/RUANJIAN/QuarkCloudDrive"',
     "网盘",
     ""),
    ("PikPak", r"E:\RUANJIAN\PikPak", "local_tool",
     "PikPak：见 path",
     r'test -d "E:/RUANJIAN/PikPak"',
     "网盘",
     ""),
    # ===== 网络 / 代理 =====
    ("Clash(ikuuu)", r"E:\LIULANQI\Clash.for.Windows-0.20.16-ikuuu", "local_tool",
     "Clash(ikuuu)：见 path",
     r'test -d "E:/LIULANQI/Clash.for.Windows-0.20.16-ikuuu"',
     "代理客户端",
     "★在 E:\\LIULANQI\\。"),
    ("ikuuu_vpn", r"E:\xhu\ikuuu_vpn-0.17.3-5712bda6-windows-amd64.exe", "local_tool",
     "ikuuu_vpn：见 path",
     r'test -f "E:/xhu/ikuuu_vpn-0.17.3-5712bda6-windows-amd64.exe"',
     "VPN 客户端",
     ""),
    ("同花顺期货通", r"E:\RUANJIAN\同花顺期货通", "local_tool",
     "同花顺期货通：见 path",
     r'test -d "E:/RUANJIAN/同花顺期货通"',
     "期货行情/交易",
     ""),
    # ===== AI 审计 / 自动化 =====
    ("多agent指挥agentctl", r"E:\RUANJIAN\ai-audit\agentctl.py", "local_tool",
     "多agent指挥agentctl：见 path",
     r'test -f "E:/RUANJIAN/ai-audit/agentctl.py"',
     "指挥OpenClaw main(grok),taskbox投递豆包",
     "python agentctl.py health/run/taskbox/mem-read/mem-write。"),
    ("GUI自动化my_gui", r"E:\RUANJIAN\ai-audit\my_gui.py", "local_tool",
     "GUI自动化my_gui：见 path",
     r'test -f "E:/RUANJIAN/ai-audit/my_gui.py"',
     "截图,点击,键盘,找图,窗口管理",
     "gui_loop 默认视觉通道写死 packy 已403，活通道用 zhipu glm-4v-flash(max_tokens≤1024)。"),
    ("记忆中枢", r"E:\RUANJIAN\memory_hub", "local_tool",
     "gateway.py / mem.py / memsearch.py / mcp_server.py 均在 E:\\RUANJIAN\\memory_hub\\",
     r'test -f "E:/RUANJIAN/memory_hub/mcp_server.py"',
     "跨会话记忆,混合检索(向量+关键词+RRF+精排),投影到 WorkBuddy 槽位,MCP Server(add_memories/search_memory/list_memories/delete_all_memories)",
     "★必须用 E:\\RUANJIAN\\memory_hub\\.venv-memory\\Scripts\\python.exe 跑检索/rebuild，默认 python 无 chromadb 会静默退化成 LIKE。"
     "真源 memory.db(facts 250 + tool_assets 66)。投影产物 ~/.workbuddy/MEMORY.md，预算 3980 字符。"
     "写记忆 gateway.py remember，写完必跑 rebuild。"),
    ("技能库", r"C:\Users\<USER>\.workbuddy\skills", "local_tool",
     "技能库：见 path",
     r'test -d "C:/Users/<USER>/.workbuddy/skills"',
     "agent 技能(SKILL.md 标准),跨账号互通",
     "用户级技能目录。国际版 WorkBuddy 通过 junction 指向同一份。"),

    # ===== C 盘资产（2026-09-15 第二轮普查补录）=====
    ("Microsoft Edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "local_tool",
     "msedge.exe；无头打印：--headless --disable-gpu --print-to-pdf=<绝对路径>",
     r'test -f "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"',
     "浏览器,无头打印PDF,headless截图",
     "★系统自带,是「无 LibreOffice 时把 HTML 转 PDF」的兜底方案。"
     "--print-to-pdf 必须写 Windows 反斜杠绝对路径。"),
    ("Visual Studio Code", r"C:\Users\<USER>\AppData\Local\Programs\Microsoft VS Code\Code.exe", "local_tool",
     "Code.exe",
     r'test -f "C:/Users/<USER>/AppData/Local/Programs/Microsoft VS Code/Code.exe"',
     "代码编辑器,插件扩展",
     "★在 AppData\\Local\\Programs 下，不在 Program Files。"),
    ("Python 3.10(系统)", r"C:\Users\<USER>\AppData\Local\Programs\Python\Python310\python.exe", "runtime",
     "python.exe",
     r'test -f "C:/Users/<USER>/AppData/Local/Programs/Python/Python310/python.exe"',
     "Python 运行时(系统版)",
     "★优先用 WorkBuddy 托管的 3.13.12。"),
    ("Windows Terminal", r"C:\Users\<USER>\AppData\Local\Microsoft\WindowsApps\wt.exe", "local_tool",
     "wt.exe",
     r'test -f "C:/Users/<USER>/AppData/Local/Microsoft/WindowsApps/wt.exe"',
     "终端",
     ""),
    ("winget", r"C:\Users\<USER>\AppData\Local\Microsoft\WindowsApps\winget.exe", "local_tool",
     "winget install/search/list",
     r'test -f "C:/Users/<USER>/AppData/Local/Microsoft/WindowsApps/winget.exe"',
     "包管理器,装机/查已装软件",
     "★查「本机装了什么」可用 winget list，比扫目录权威。"),
    ("NanaZip", r"C:\Users\<USER>\AppData\Local\Microsoft\WindowsApps\NanaZip.exe", "local_tool",
     "NanaZip.exe",
     r'test -f "C:/Users/<USER>/AppData/Local/Microsoft/WindowsApps/NanaZip.exe"',
     "压缩解压(现代 7-Zip 分支)",
     ""),
    ("Bandizip", r"C:\Users\<USER>\AppData\Local\Microsoft\WindowsApps\bandizip.exe", "local_tool",
     "bandizip.exe",
     r'test -f "C:/Users/<USER>/AppData/Local/Microsoft/WindowsApps/bandizip.exe"',
     "压缩解压",
     ""),
    ("Cheat Engine", r"C:\Program Files\Cheat Engine\Cheat Engine.exe", "local_tool",
     "Cheat Engine.exe",
     r'test -f "C:/Program Files/Cheat Engine/Cheat Engine.exe"',
     "内存扫描,单机游戏修改",
     "目录下有 <SAMPLE_X> Engine.exe(第三方包装器特征，勿用)。"),
    ("Npcap", r"C:\Program Files\Npcap\NPFInstall.exe", "driver",
     "NPFInstall.exe",
     r'test -f "C:/Program Files/Npcap/NPFInstall.exe"',
     "抓包驱动(Wireshark 依赖)",
     "Wireshark 抓包依赖它。另有旧版 WinPcap。"),
    ("WinPcap", r"C:\Program Files (x86)\WinPcap\rpcapd.exe", "driver",
     "rpcapd.exe",
     r'test -f "C:/Program Files (x86)/WinPcap/rpcapd.exe"',
     "抓包驱动(旧版)",
     ""),
    ("AntiCheatExpert", r"C:\Program Files\AntiCheatExpert\ACE-Service64.exe", "local_tool",
     "ACE-Service64.exe(服务)",
     r'test -f "C:/Program Files/AntiCheatExpert/ACE-Service64.exe"',
     "腾讯游戏反作弊服务",
     "★排查「某进程为什么被拦/被结束」时要注意它在跑。"),
    ("CF活动助手", r"C:\Program Files\cfzhushou\CF活动助手6.0.3.exe", "local_tool",
     "CF活动助手6.0.3.exe",
     r'test -f "C:/Program Files/cfzhushou/CF活动助手6.0.3.exe"',
     "CF 游戏辅助",
     ""),
    ("Object Fix Zip", r"C:\Program Files (x86)\Object Fix Zip\ObjectFixZip.exe", "local_tool",
     "ObjectFixZip.exe",
     r'test -f "C:/Program Files (x86)/Object Fix Zip/ObjectFixZip.exe"',
     "损坏 zip 修复",
     ""),
    ("REDRAGON G62驱动", r"C:\Program Files (x86)\REDRAGON G62\OemDrv.exe", "driver",
     "OemDrv.exe",
     r'test -f "C:/Program Files (x86)/REDRAGON G62/OemDrv.exe"',
     "红龙 G62 键鼠驱动",
     ""),
    ("DrvCeo驱动总裁", r"C:\DrvCeonw\DrvCeo.exe", "local_tool",
     "DrvCeo.exe",
     r'test -f "C:/DrvCeonw/DrvCeo.exe"',
     "驱动安装/更新",
     "★在 C:\\DrvCeonw\\（非标准目录）。"),
    ("Discord PTB", r"C:\Users\<USER>\AppData\Local\Discord\app-1.0.9256\Discord.exe", "local_tool",
     "Discord.exe",
     r'test -f "C:/Users/<USER>/AppData/Local/Discord/app-1.0.9256/Discord.exe"',
     "即时通信(公测版)",
     "★版本号在路径里，升级后会变，校验会失活属正常。"),
    ("YTDownloader", r"C:\Users\<USER>\.ytDownloader\ytdlp.exe", "local_tool",
     "ytdlp.exe",
     r'test -f "C:/Users/<USER>/.ytDownloader/ytdlp.exe"',
     "视频下载(yt-dlp 封装)",
     ""),
    ("WCHISPTool", r"C:\WCH\WCHISPTool", "local_tool",
     "WCHISPStudio.exe",
     r'test -d "C:/WCH/WCHISPTool"',
     "WCH 芯片 ISP 烧录(自制板)",
     "★在 C:\\WCH\\。配 CH341/CH32 用。"),
    ("Lenovo Service Bridge", r"C:\Users\<USER>\AppData\Local\Programs\Lenovo\Lenovo Service Bridge\CreateWTSTask.exe", "local_tool",
     "CreateWTSTask.exe",
     r'test -f "C:/Users/<USER>/AppData/Local/Programs/Lenovo/Lenovo Service Bridge/CreateWTSTask.exe"',
     "联想设备服务",
     ""),
    # ===== E 盘补录（第一轮漏掉的）=====
    ("微信", r"E:\RUANJIAN\WEIXIN\Weixin.exe", "local_tool",
     "Weixin.exe",
     r'test -f "E:/RUANJIAN/WEIXIN/Weixin.exe"',
     "即时通信",
     "★新版微信(Weixin.exe)，目录 E:\\RUANJIAN\\WEIXIN\\。"),
    ("网易云音乐", r"E:\RUANJIAN\CloudMusic\cloudmusic.exe", "local_tool",
     "cloudmusic.exe",
     r'test -f "E:/RUANJIAN/CloudMusic/cloudmusic.exe"',
     "音乐播放",
     ""),
    ("豆包", r"E:\RUANJIAN\Doubao\Doubao.exe", "local_tool",
     "Doubao.exe",
     r'test -f "E:/RUANJIAN/Doubao/Doubao.exe"',
     "AI 对话客户端",
     "agentctl 的 taskbox 可向它投递任务；无自动取件，需在会话触发一次。"),
    ("迅雷", r"E:\RUANJIAN\Thunder\Program\ThunderStart.exe", "local_tool",
     "ThunderStart.exe",
     r'test -f "E:/RUANJIAN/Thunder/Program/ThunderStart.exe"',
     "下载",
     ""),
]


def audit():
    """扫盘 + 报告哪些资产没入库"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT name, path FROM tool_assets")
    known = {}
    for n, p in cur.fetchall():
        known[n] = p
    conn.close()

    print(f"{'状态':<6} {'名称':<24} {'路径'}")
    print("-" * 100)
    miss, ok = [], []
    for a in ASSETS:
        name, path = a[0], a[1]
        exists = os.path.exists(path.replace("/", os.sep))
        tag = "OK" if exists else "MISS"
        if exists:
            ok.append(name)
        else:
            miss.append((name, path))
        print(f"{tag:<6} {name:<24} {path}")
    print("-" * 100)
    print(f"活 {len(ok)} / 缺 {len(miss)}")
    for n, p in miss:
        print(f"  缺: {n} -> {p}")

    new = [n for n, *_ in ASSETS if n not in known]
    print(f"\n库内已有 {len(known)} 条，本次候选 {len(ASSETS)} 条，新增 {len(new)} 条：")
    for n in new:
        print("  +", n)
    stale = [n for n in known if n not in [a[0] for a in ASSETS]]
    if stale:
        print(f"库内有但本次未覆盖 {len(stale)} 条：{stale}")


def _run_verify(cmd: str, path: str) -> bool:
    """校验。支持 test -f / test -d / test -e，纯 Python 实现，不依赖外部 shell。"""
    import re
    m = re.match(r'\s*test\s+(-[fde])\s+"?([^"]+)"?\s*$', cmd or "")
    if m:
        flag, target = m.group(1), m.group(2).replace("/", os.sep)
        if flag == "-f":
            return os.path.isfile(target)
        if flag == "-d":
            return os.path.isdir(target)
        return os.path.exists(target)
    # 未知命令：退化为路径存在性
    return os.path.exists((path or "").replace("/", os.sep))


def verify():
    """对库内每条跑校验，更新 last_verified_at / status"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, name, path, verification_method FROM tool_assets ORDER BY id")
    rows = cur.fetchall()
    print(f"{'结果':<6} {'ID':<4} {'名称':<22} 校验")
    print("-" * 88)
    good = bad = 0
    results = []
    for r in rows:
        vm = r["verification_method"] or f'test -e "{r["path"].replace(chr(92), "/")}"'
        alive = _run_verify(vm, r["path"])
        if alive:
            good += 1
        else:
            bad += 1
        results.append((r["id"], alive, vm, r["name"]))
        print(f"{'OK' if alive else 'DEAD':<6} {r['id']:<4} {r['name']:<22} {vm}")
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for rid, alive, vm, _ in results:
        cur.execute(
            "UPDATE tool_assets SET last_verified_at=?, verification_method=?, "
            "status=?, updated_at=? WHERE id=?",
            (now, vm, "active" if alive else "missing", now, rid),
        )
    conn.commit()
    print("-" * 88)
    print(f"存活 {good} / 失活 {bad}  （校验时间 {now}）")
    conn.close()
    return good, bad


def seed():
    """把 ASSETS 写入 tool_assets（upsert by name）"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    added = updated = 0
    for a in ASSETS:
        # 兼容 (name,path,kind[,entry],verify,caps,notes)
        if len(a) == 7:
            name, path, kind, entry, ver, caps, notes = a
        else:
            name, path, kind, ver, caps, notes = a
            entry = ""
        exists = os.path.exists(path.replace("/", os.sep))
        cur.execute("SELECT id FROM tool_assets WHERE name=?", (name,))
        row = cur.fetchone()
        status = "active" if exists else "missing"
        if row:
            cur.execute(
                """UPDATE tool_assets SET path=?, type=?, entrypoint=?,
                   capabilities=?, prerequisites=?, verification_method=?,
                   last_verified_at=?, status=?, updated_at=?
                   WHERE id=?""",
                (path, kind, entry, caps, notes, ver, now, status, now, row[0]),
            )
            updated += 1
        else:
            uid = "tool-" + dt.datetime.now().strftime("%Y%m%d%H%M%S") + "-" + os.urandom(6).hex()
            cur.execute(
                """INSERT INTO tool_assets
                   (uid,name,aliases,type,status,path,entrypoint,capabilities,
                    known_failures,prerequisites,last_verified_at,verification_method,
                    source,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid, name, name, kind, status, path,
                 entry, caps, "[]", notes, now, ver, "tool_audit.py", now, now),
            )
            added += 1
    conn.commit()
    print(f"新增 {added} 条，更新 {updated} 条，共 {added+updated}")
    conn.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if mode == "audit":
        audit()
    elif mode == "verify":
        verify()
    elif mode == "seed":
        seed()
    else:
        print(__doc__)
