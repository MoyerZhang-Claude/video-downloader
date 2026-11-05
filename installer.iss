; 链接解析下载器 安装包（Inno Setup 6）
; 由 打包.bat 调用：ISCC /DAppVersion=x.y.z installer.iss
; 先运行 PyInstaller 生成 dist\链接解析下载器，再打成安装包 dist\installer\LinkDownloader-x.y.z-windows-setup.exe
;
; 装在当前用户的 %LOCALAPPDATA%\Programs 下，不需要管理员权限。
; 设置、历史记录、登录信息、头像、日志在 %APPDATA%\VideoDownloader，卸载时保留（重装后还在）。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "链接解析下载器"
#define AppExe "链接解析下载器.exe"
#define SrcDir "dist\链接解析下载器"

[Setup]
; AppId 不要改：升级安装靠它认出是同一个软件
AppId={{6F1E3C5A-8B2D-4C7E-9A41-2D5B7E0C9F13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=MoyerZhang-Claude
AppPublisherURL=https://github.com/MoyerZhang-Claude/video-downloader
AppSupportURL=https://github.com/MoyerZhang-Claude/video-downloader/issues
AppUpdatesURL=https://github.com/MoyerZhang-Claude/video-downloader/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist\installer
OutputBaseFilename=LinkDownloader-{#AppVersion}-windows-setup
SetupIconFile=app\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; 升级时如果软件开着，先关掉它（托盘里的也算）
CloseApplications=force
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}

[Languages]
Name: "chs"; MessagesFile: "installer\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建快捷方式"; GroupDescription: "快捷方式："

[Files]
; 只装程序本身；就算 dist 文件夹里有旧版留下的个人数据，也不会打进安装包
Source: "{#SrcDir}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SrcDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SrcDir}\ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 升级时清掉上一版的程序文件，避免新旧文件混在一起
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "现在打开{#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载前先结束还在运行的软件和网盘引擎
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im ""{#AppExe}"""; Flags: runhidden; RunOnceId: "KillApp"
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im pan-engine.exe"; Flags: runhidden; RunOnceId: "KillEngine"

[Messages]
chs.WelcomeLabel2=将在你的电脑上安装 [name/ver]。%n%n一个输入框，解析视频和网盘分享链接。%n%n设置、历史记录和登录信息保存在你的用户数据文件夹里，以后升级或重装都不会丢。
