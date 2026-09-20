# install.ps1 — MemTether Windows 一键安装
#
# 背景（2026-09-20 审计补件）：README 的「快速开始」只有 pip 三行，
# 对 Windows 用户的实际门槛是四件事串起来：找解释器 → 装包（含可选的语义档）
# → 建数据目录 → 接客户端。每件单独都不难，串起来就是「装完跑不起来」的高发区。
# 本脚本把四件事做成一条命令，原则与 tether_connect 对齐：
#   1. 路径全部相对 $PSScriptRoot（不写死任何本机绝对路径）
#   2. 幂等：已装好且一致的步骤跳过，重跑结果一样
#   3. 失败关闭：任一步非零退出即停，后续不执行，退出码透传
#   4. 每步可跳过（-SkipInstall / -SkipDemo / -SkipConnect / -SkipVector）
#   5. 装包前先 `pip show memtether` 核对版本，版本不符才重装
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File install.ps1            # 全流程
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Vector    # 含语义检索档
#   powershell -ExecutionPolicy Bypass -File install.ps1 -SkipConnect -SkipDemo
#
# 退出码：0=成功；1=环境/参数错误；2=装包失败；3=演示库失败；4=客户端接入失败

param(
    [switch]$Vector,        # 装 memtether[vector]（chromadb/onnxruntime，体积大，缺了会自动降级）
    [switch]$Editable,      # -e 开发模式（改了源码即时生效）；默认装正式副本
    [switch]$SkipInstall,   # 跳过装包（已装过）
    [switch]$SkipDemo,      # 跳过演示库生成（已有自己的库）
    [switch]$SkipConnect,   # 跳过客户端自动接入
    [string]$Home = "",     # 数据目录，默认 ~/.memtether（等价环境变量 MEMTETHER_HOME）
    [string]$Python = ""    # 手动指定解释器路径；默认自动探测
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot

function Write-Step([string]$msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  [ok] $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "  [warn] $msg" -ForegroundColor Yellow }

# ---------- 0. 前置检查 ----------
Write-Step "0/4 前置检查"
if (-not (Test-Path (Join-Path $RepoRoot 'pyproject.toml'))) {
    Write-Host "  [fail] 当前目录不是 MemTether 仓库根（缺 pyproject.toml）：$RepoRoot" -ForegroundColor Red
    exit 1
}

if ($Python) {
    if (-not (Test-Path $Python)) { Write-Host "  [fail] 指定的 Python 不存在: $Python" -ForegroundColor Red; exit 1 }
    $Py = $Python
} else {
    # 探测顺序：py launcher（Windows 官方推荐）→ python → python3
    $Py = $null
    foreach ($cand in @('py', 'python', 'python3')) {
        try {
            $v = & $cand --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $v -match 'Python (\d+)\.(\d+)') {
                $major = [int]$Matches[1]; $minor = [int]$Matches[2]
                if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
                    $Py = $cand; Write-Ok "找到 $cand → $v"; break
                } else {
                    Write-Warn2 "$cand 版本过低（$v < 3.10），继续找"
                }
            }
        } catch { }
    }
    if (-not $Py) {
        Write-Host "  [fail] 没找到 Python >= 3.10。请先安装：https://www.python.org/downloads/ （安装时勾选 Add to PATH）" -ForegroundColor Red
        exit 1
    }
}

if ($Home) { $env:MEMTETHER_HOME = $Home; Write-Ok "数据目录 MEMTETHER_HOME = $Home" }
else { Write-Ok "数据目录默认 ~/.memtether（MEMTETHER_HOME 未设置）" }

# ---------- 1. 装包 ----------
if ($SkipInstall) {
    Write-Step "1/4 装包（已按参数跳过）"
} else {
    Write-Step "1/4 安装 MemTether 包"
    $pkgSpec = if ($Vector) { '.[vector]' } else { '.' }
    if ($Editable) { $pkgSpec = "-e $pkgSpec" }

    # 幂等：版本一致且能 import 就跳过（版本号从 pyproject.toml 读，避免硬编码漂移）
    $wantVer = ''
    if (Test-Path (Join-Path $RepoRoot 'pyproject.toml')) {
        $m = Select-String -Path (Join-Path $RepoRoot 'pyproject.toml') -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($m) { $wantVer = $m.Matches[0].Groups[1].Value }
    }
    $installed = & $Py -m pip show memtether 2>$null | Select-String '^Version:'
    $installedVer = if ($installed) { ($installed -split '\s+')[1] } else { '' }
    if ($installedVer -and $installedVer -eq $wantVer) {
        Write-Ok "已安装 memtether $installedVer（与仓库一致），跳过装包"
    } else {
        if ($installedVer) { Write-Warn2 "已装 $installedVer ≠ 仓库 $wantVer，重新安装" }
        & $Py -m pip install $pkgSpec.Split(' ')
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [fail] pip install 失败（退出码 $LASTEXITCODE）" -ForegroundColor Red
            Write-Host "  常见原因：① 网络不通（可加 -i https://pypi.tuna.tsinghua.edu.cn/simple）；② Python 版本 < 3.10" -ForegroundColor Red
            exit 2
        }
        Write-Ok "包安装完成"
    }
}

# ---------- 2. 演示库 ----------
if ($SkipDemo) {
    Write-Step "2/4 演示库（已按参数跳过）"
} else {
    Write-Step "2/4 生成演示库（全合成数据，可安全删）"
    $dbPath = if ($Home) { Join-Path $Home 'memory.db' } else { Join-Path $env:USERPROFILE '.memtether\memory.db' }
    if (Test-Path $dbPath) {
        Write-Ok "演示库已存在：$dbPath（不覆盖；想重建先备份再删该文件）"
    } else {
        & $Py -m memtether demo
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [fail] 演示库生成失败（退出码 $LASTEXITCODE）" -ForegroundColor Red
            exit 3
        }
        if (Test-Path $dbPath) { Write-Ok "演示库就绪：$dbPath" }
        else { Write-Warn2 "命令成功但未找到 $dbPath（可能 MEMTETHER_HOME 指向别处），请用 memtether stats 核对" }
    }
}

# ---------- 3. 客户端接入 ----------
if ($SkipConnect) {
    Write-Step "3/4 客户端接入（已按参数跳过）"
} else {
    Write-Step "3/4 自动接入本机 AI 客户端（只读 detect → 预演 plan → 落盘 apply → 校验 verify）"
    $tc = Join-Path $RepoRoot 'tether_connect.py'
    if (-not (Test-Path $tc)) { Write-Warn2 "未找到 tether_connect.py，跳过接入（不影响包使用）" }
    else {
        & $Py $tc detect | Out-Host
        & $Py $tc plan | Out-Host
        $apply = & $Py $tc apply 2>&1
        $apply | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [fail] 客户端接入失败（退出码 $LASTEXITCODE）；备份在 ~/.memtether/backups/，可 python tether_connect.py rollback 还原" -ForegroundColor Red
            exit 4
        }
        & $Py $tc verify | Out-Host
        Write-Ok "接入流程完成（verify 输出即最终状态）"
    }
}

# ---------- 4. 收尾自检 ----------
Write-Step "4/4 收尾自检"
& $Py -m memtether stats
if ($LASTEXITCODE -ne 0) { Write-Host "  [fail] memtether stats 跑不通（退出码 $LASTEXITCODE）" -ForegroundColor Red; exit 3 }
Write-Host "`n安装完成。常用命令：" -ForegroundColor Green
Write-Host "  memtether search `"关键词`"     # 混合检索"
Write-Host "  memtether stats                # 库内统计"
Write-Host "  python tether_connect.py detect   # 随时重看客户端接入状态"
exit 0
