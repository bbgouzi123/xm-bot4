@echo off
chcp 65001 >nul 2>&1
title xm-bot4 诊断模式
color 0F

echo.
echo ╔════════════════════════════════════════════════════════╗
echo ║          xm-bot4 运行环境诊断工具                     ║
echo ║          如果双击 exe 无反应，请用此工具排查           ║
echo ╚════════════════════════════════════════════════════════╝
echo.

echo [1/6] 操作系统信息
echo ──────────────────────────────────────────
systeminfo | findstr /B /C:"OS 名称" /C:"OS Name" /C:"OS Version" /C:"OS 版本" /C:"系统类型" /C:"System Type" 2>nul
echo.

echo [2/6] 检查 WebView2 运行时
echo ──────────────────────────────────────────
set WV2_FOUND=0
reg query "HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv 2>nul && set WV2_FOUND=1
if %WV2_FOUND%==0 reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv 2>nul && set WV2_FOUND=1
if %WV2_FOUND%==0 reg query "HKCU\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv 2>nul && set WV2_FOUND=1
if %WV2_FOUND%==0 (
    echo [!!] 未检测到 WebView2 运行时！
    echo     请从以下地址下载安装：
    echo     https://go.microsoft.com/fwlink/p/?LinkId=2124703
) else (
    echo [OK] WebView2 运行时已安装
)
echo.

echo [3/6] 检查 .NET Framework
echo ──────────────────────────────────────────
reg query "HKLM\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" /v Release 2>nul
if errorlevel 1 (
    echo [!!] 未检测到 .NET Framework 4.x！
) else (
    echo [OK] .NET Framework 4.x 已安装
)
echo.

echo [4/6] 检查 VC++ 运行时
echo ──────────────────────────────────────────
if exist "%SystemRoot%\System32\msvcp140.dll" (
    echo [OK] msvcp140.dll 存在 - VC++ 运行时已安装
) else (
    echo [!!] msvcp140.dll 缺失！请安装 Visual C++ 2015-2022 Redistributable
    echo     https://aka.ms/vs/17/release/vc_redist.x64.exe
)
if exist "%SystemRoot%\System32\vcruntime140.dll" (
    echo [OK] vcruntime140.dll 存在
) else (
    echo [!!] vcruntime140.dll 缺失！
)
if exist "%SystemRoot%\System32\vcruntime140_1.dll" (
    echo [OK] vcruntime140_1.dll 存在
) else (
    echo [!!] vcruntime140_1.dll 缺失！（部分 Python C 扩展需要此文件）
)
echo.

echo [5/6] 检查杀毒软件状态
echo ──────────────────────────────────────────
echo 请手动检查:
echo   - Windows 安全中心 → 病毒和威胁防护 → 保护历史记录
echo   - 查看是否有 xm-bot4.exe 被标记/隔离
echo   - 如有其他杀毒软件（360、火绒等），请检查其隔离区
echo.

echo [6/6] 检查已有日志文件
echo ──────────────────────────────────────────
set LOG_DIR=%APPDATA%\xm-bot4\logs
if exist "%LOG_DIR%" (
    echo [OK] 日志目录存在: %LOG_DIR%
    echo 包含以下文件:
    dir /B "%LOG_DIR%" 2>nul
    echo.
    if exist "%LOG_DIR%\early_boot.log" (
        echo --- early_boot.log 最后 20 行 ---
        powershell -Command "Get-Content '%LOG_DIR%\early_boot.log' -Tail 20" 2>nul
        echo.
    )
    if exist "%LOG_DIR%\crash.log" (
        echo --- crash.log 最后 30 行 ---
        powershell -Command "Get-Content '%LOG_DIR%\crash.log' -Tail 30" 2>nul
        echo.
    )
) else (
    echo [!!] 日志目录不存在: %LOG_DIR%
    echo     这意味着程序可能从未成功启动过 Python 解释器
    echo     原因通常是: 杀毒软件拦截 / DLL 缺失 / SmartScreen 阻止
)
echo.

echo ════════════════════════════════════════════════════════
echo 诊断完成。请将以上所有输出截图发送给技术支持。
echo.
echo 是否尝试以诊断模式启动 xm-bot4？
set /P RUN_EXE=输入 Y 启动，其他键跳过: 
if /I "%RUN_EXE%"=="Y" (
    echo.
    echo 正在以诊断模式启动...
    echo （此窗口会显示程序输出，请保持打开）
    echo ────────────────────────────────────────
    cd /d "%~dp0"
    if exist "xm-bot4.exe" (
        xm-bot4.exe
        echo.
        echo ════════════════════════════════════════════════════════
        echo 程序已退出（退出码: %errorlevel%）
    ) else (
        echo [!!] 未找到 xm-bot4.exe！请将此脚本放在 exe 同目录下运行。
    )
)
echo.
pause
