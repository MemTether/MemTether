@echo off
rem install.bat — MemTether Windows 双击安装入口
rem 为什么不写 .ps1 直开：Windows 默认双击 .ps1 用记事本打开，且 PowerShell
rem 默认执行策略禁脚本；bat 中转一次，带上 -ExecutionPolicy Bypass。
rem 参数原样透传给 install.ps1，例如： install.bat -Vector
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
    echo.
    echo [FAIL] 安装未完成，退出码 %errorlevel%（1=环境 2=装包 3=演示库 4=接入）
    pause
    exit /b %errorlevel%
)
echo.
echo [OK] 全部完成。
pause
