; Inno Setup script for the local Windows build.
; scripts\build_windows.ps1 -Package runs ISCC on this file after PyInstaller.
; Output: dist\VigilCam-Setup.exe

#define MyAppName "VigilCam"
#define MyAppExeName "VigilCam.exe"
#define MyAppVersion "7.0.16"
; SourcePath ends with a backslash and may contain spaces (C:\App Dev\...).
#define DistDir SourcePath + "..\dist\VigilCam"

[Setup]
AppId={{C3E8A1F4-6B27-4D90-9A15-7F2E8D4B6C01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=VigilCam
DefaultDirName={autopf}\VigilCam
DefaultGroupName=VigilCam
DisableProgramGroupPage=yes
OutputDir="{#SourcePath}..\dist"
OutputBaseFilename=VigilCam-Setup
SetupIconFile=..\icons\VigilCam.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
CloseApplications=yes

[Tasks]
; A task is checked unless Flags includes unchecked. "checked" is not a task flag.
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start VigilCam when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VigilCam"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
