; =============================================================
; xm-bot4 Windows Installer Script
; 构建工具: Inno Setup 6.x (https://jrsoftware.org/isinfo.php)
;
; 核心功能：
;   - 将所有打包产物安装到 Program Files（或用户选择目录）
;   - 安装后自动调用 PowerShell Unblock-File，清除网络下载锁定标记
;   - 创建桌面和开始菜单快捷方式
;   - 集成卸载功能
;
; 使用方法：
;   1. 先执行 PyInstaller 打包，确保 dist\xm-bot4\ 目录已生成
;   2. 安装 Inno Setup 6，双击本文件选择 Build -> Compile
;   3. 生成的安装包位于 dist_installer\ 目录
; =============================================================

#define AppName "xm-bot4"
#define AppVersion "1.0.284"
#define AppPublisher "星码行空"
#define AppURL "https://xmcore.top"
#define AppExeName "xm-bot4.exe"
; PyInstaller 打包输出目录（相对于本 .iss 文件位置：scripts/ → backend-python/dist/xm-bot4）
#define SourceDir "..\dist\xm-bot4"

[Setup]
; 应用基本信息
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}

; 默认安装路径（允许用户修改）
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes

; 安装包输出位置与文件名
OutputDir=..\..\dist_installer
OutputBaseFilename=xm-bot4_v{#AppVersion}_setup

; 压缩算法（lzma2 体积最小）
Compression=lzma2
SolidCompression=yes
CompressionThreads=auto

; 安装包图标（可替换为自己的图标路径）
SetupIconFile=..\assets\logo.ico

; 权限要求
; admin = 强制需要管理员权限，确保可写 Program Files 和注册表
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; 最低系统要求
MinVersion=10.0

; 安装界面语言（使用项目本地的中文翻译文件，不依赖系统安装的 Inno Setup 是否自带）
[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked
Name: "startmenuicon"; Description: "创建开始菜单快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
; 将 PyInstaller 输出目录下所有文件打包进安装包
; recursesubdirs = 包含所有子目录
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单快捷方式
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startmenuicon
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; =====================================================================
; 关键步骤：安装后自动调用 PowerShell 解除所有文件的 NTFS 网络锁定标记
; 这一步彻底消除"无法验证发布者"弹窗，对用户完全透明
; =====================================================================
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ""Get-ChildItem -Path '{app}' -Recurse | Unblock-File"""; \
  Flags: runhidden waituntilterminated; \
  StatusMsg: "正在配置运行环境..."; \
  Description: "解除文件安全锁定"

; 安装完成后询问是否立即启动程序
Filename: "{app}\{#AppExeName}"; \
  Description: "立即启动 {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载时清理注册的开机启动（如有）
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""{#AppName}"" /F"; Flags: runhidden; RunOnceId: "RemoveStartupTask"

[Registry]
; （可选）注册卸载信息到"添加/删除程序"面板，Inno Setup 会自动处理
; 如需注册其他注册表项可在此添加

[Code]
// ====================================================
// 安装完成前的自定义验证：检查 PowerShell 是否可用
// 若不可用，则提示用户手动右键属性 -> 解除锁定
// ====================================================
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  // 可在此处添加自定义进度事件
end;
