; Inno Setup 安裝檔：安裝到使用者資料夾，不需要系統管理員，自動更新才能覆蓋
#ifndef AppVersion
#define AppVersion "0.0.0"
#endif
[Setup]
AppId={{7E2D3B7C-3C7B-4C7E-9B1A-5E2D1C0B9A81}
AppName=VV 實穿草稿產生器
AppVersion={#AppVersion}
AppPublisher=VV DOUBLE
DefaultDirName={localappdata}\Programs\VVReels
DefaultGroupName=VV DOUBLE
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=VVReels-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\VV實穿草稿產生器.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他："

[Files]
Source: "..\dist\VV實穿草稿產生器\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\VV 實穿草稿產生器"; Filename: "{app}\VV實穿草稿產生器.exe"
Name: "{autodesktop}\VV 實穿草稿產生器"; Filename: "{app}\VV實穿草稿產生器.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VV實穿草稿產生器.exe"; Description: "立即開啟"; Flags: nowait postinstall skipifsilent
