; Employee Monitoring — Inno Setup installer (Phase 10)
; Fully self-contained bundle produced by installer/build.spec (PyInstaller --onedir windowed).
; No Python, SQLite, or external runtime required on the target machine.
;
; Build order on Windows:
;   uv sync --group dev
;   uv run pyinstaller installer/build.spec --noconfirm --clean
;   iscc installer/installer.iss
;   ; output: installer/dist/EmployeeMonitoring-Setup-0.1.0.exe
;
; Per-user install (no admin) — data lives in %LOCALAPPDATA%\EmployeeMonitoring\*.
; Install dir != data dir (docs/DATA_MODEL.md §Storage layout). Uninstaller
; never deletes the data dir; the user is told where it lives.
;
; Code signing (Authenticode):
;   Uncomment SignTool lines and set SIGNTOOL env / define SIGNTOOL_PFX.
;   Without a cert the build is unsigned (docs/SECURITY_PRIVACY.md:59) —
;   SmartScreen will warn. The stub stays so adding a cert later is trivial.

#define MyAppName "Employee Monitoring"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "EmployeeMonitoring Inc."
#define MyAppURL "https://example.com"
#define MyAppExeName "EmployeeMonitoring.exe"

; Allow overriding version from command line: iscc /DMyAppVersion=1.2.3 installer.iss
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

[Setup]
AppId={{8E2B8E2A-6B3C-4A1E-9F8C-EmployeeMonitoring}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Employee Monitoring desktop agent
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
; Per-user — no elevation required (installer never writes to Program Files or HKLM).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=dist
OutputBaseFilename=EmployeeMonitoring-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icons\app.ico
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; Keep uninstaller small; bundle is already compressed.
InternalCompressLevel=max
; For unsigned dev builds SmartScreen will show "Unknown publisher" — expected.
; Signed builds: iscc /Ssigntool="signtool.exe sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f $qcert.pfx$q /p $qpass$q $f" installer.iss
;SignTool=signtool
;SignedUninstaller=yes
ArchitecturesInstallIn64BitMode=x64compatible
ShowLanguageDialog=no
AppMutex=EmployeeMonitoringSingleInstance
; Keep previous versions' data; installer only replaces {app}
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start {#MyAppName} when I log in (recommended)"; GroupDescription: "Startup:"; Flags: unchecked
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; PyInstaller --onedir output — everything needed at runtime.
; Excludes dev artefacts (.pyc caches are inside base_library.zip — kept).
Source: "..\dist\EmployeeMonitoring\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Icon for uninstaller display (also inside dist, but keep explicit for setup)
Source: "..\assets\icons\app.ico"; DestDir: "{app}\assets\icons"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Registry]
; Autostart — per-user Run key, launched minimized to tray (app/main.py --minimized).
; Value is quoted; Inno handles spaces correctly.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "EmployeeMonitoring"; ValueData: """{app}\{#MyAppExeName}"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart
; Also provide a non-task path: the app's own StartupManager (app/infrastructure/system/startup.py)
; can enable/disable this key at runtime — installer just seeds the initial state.

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
; DO NOT delete %LOCALAPPDATA%\EmployeeMonitoring — user data, screenshots, DB.
; The uninstaller purposely leaves it behind and the final page tells the user where it is.
; If the user wants a full wipe they delete that folder manually (documented in README/data-retention).

[Code]
var
  DataDirPage: TOutputMsgWizardPage;

function GetDataDir(Param: String): String;
begin
  Result := ExpandConstant('{localappdata}\EmployeeMonitoring');
end;

procedure InitializeWizard;
begin
  DataDirPage := CreateOutputMsgPage(
    wpSelectDir,
    'Local data location',
    'Where your monitoring data is stored',
    'Your work sessions, screenshots, and logs are stored per-user and are NOT inside the install folder:'#13#10 +
    ExpandConstant('{localappdata}\EmployeeMonitoring\') + #13#10#13#10 +
    'This folder is kept when you uninstall. Delete it manually if you want a full wipe. '#13#10 +
    'See README “Uninstall & data retention”.'
  );
end;

function InitializeUninstall(): Boolean;
begin
  // Best-effort: ask the running app to close via mutex/CloseApplications.
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\EmployeeMonitoring');
    // Intentionally do not delete DataDir. Show where it lives.
    MsgBox(
      'Employee Monitoring has been uninstalled.' + #13#10#13#10 +
      'Your local data was kept at:'#13#10 + DataDir + #13#10#13#10 +
      'Delete that folder manually if you want to remove all local history.',
      mbInformation, MB_OK
    );
  end;
end;
