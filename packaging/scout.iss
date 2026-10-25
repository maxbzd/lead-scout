; Установщик Scout для Windows (Inno Setup). Ставится в профиль пользователя, права администратора не нужны.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B7A1D0C4-5C6E-4F7E-9E0B-5C0070A1B001}
AppName=Scout
AppVersion={#AppVersion}
AppPublisher=Scout
DefaultDirName={autopf}\Scout
DefaultGroupName=Scout
PrivilegesRequired=lowest
OutputDir=..\installer
OutputBaseFilename=Scout-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Scout.exe
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Значок на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "..\dist\Scout\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Scout"; Filename: "{app}\Scout.exe"
Name: "{autodesktop}\Scout"; Filename: "{app}\Scout.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Scout.exe"; Description: "Запустить Scout"; Flags: nowait postinstall skipifsilent
