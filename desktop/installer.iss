; figapp の Windows 用インストーラー（Inno Setup 6）。GitHub Actions がリポジトリのいちばん上で
;   iscc /DAppVersion=1.14 desktop\installer.iss
; を実行して installer\figapp-1.14-windows-setup.exe を作る。管理者の権限は要らない（自分のユーザーのフォルダに入れる）。
#ifndef AppVersion
  #define AppVersion "0"
#endif

[Setup]
AppId={{6F0B4C61-2C61-4C6B-9C67-7E3A9F1D2B10}
AppName=figapp
AppVersion={#AppVersion}
AppVerName=figapp {#AppVersion}
AppPublisher=figapp
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\figapp
DefaultGroupName=figapp
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\installer
OutputBaseFilename=figapp-{#AppVersion}-windows-setup
SetupIconFile=icons\figapp.ico
UninstallDisplayIcon={app}\figapp.exe
UninstallDisplayName=figapp
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; 古い版の部品を消してから入れる（設定と色の一覧は %APPDATA%\figapp にあるので消えない）
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\figapp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\figapp"; Filename: "{app}\figapp.exe"
Name: "{autodesktop}\figapp"; Filename: "{app}\figapp.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\figapp.exe"; Description: "{cm:LaunchProgram,figapp}"; Flags: nowait postinstall skipifsilent
