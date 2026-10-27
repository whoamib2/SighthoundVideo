; Inno Setup script for the local Windows build.
; scripts\build_windows.ps1 -Package runs ISCC on this file after PyInstaller.
; Output: dist\SighthoundVideo-Setup.exe

#define MyAppName "Sighthound Video"
#define MyAppExeName "SighthoundVideo.exe"
#define MyAppVersion "7.0.16"
; SourcePath ends with a backslash and may contain spaces (C:\App Dev\...).
#define DistDir "{#SourcePath}..\dist\SighthoundVideo"

[Setup]
AppId={{8F3A1C2E-6B47-4D19-9E5A-2C7B0D4F1A83}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Sighthound
DefaultDirName={autopf}\Sighthound Video
DefaultGroupName=Sighthound Video
DisableProgramGroupPage=yes
OutputDir="{#SourcePath}..\dist"
OutputBaseFilename=SighthoundVideo-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checked
Name: "startup"; Description: "Start Sighthound Video when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Sighthound Video"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
