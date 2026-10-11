; Alfred's installer, for Inno Setup 6.3 or newer. packaging\installer.ps1 compiles it from dist\Alfred:
;   ISCC.exe /DAppVersion=0.1.0 /DDistDir=<dist\Alfred> /DBuildDir=<build> /DOutputDir=<dist>
;            [/DWebView2Setup=<MicrosoftEdgeWebview2Setup.exe>] packaging\alfred.iss
; -> dist\Alfred-Setup-<version>.exe
;
; Per-user: no administrator prompt; installs into %LOCALAPPDATA%\Programs\Alfred with a Start menu
; shortcut (and, if ticked, a desktop one) and registers an uninstaller in Apps & features. Windows 10
; 1809 or newer, x64. Silent install: Alfred-Setup-<version>.exe /VERYSILENT /CURRENTUSER [/DIR=<folder>].
; Silent uninstall: unins000.exe /VERYSILENT [/REMOVEUSERDATA] (that switch also removes settings and keys).

#if VER < EncodeVer(6, 3, 0, 0)
  #error Alfred's installer needs Inno Setup 6.3 or newer.
#endif

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir AddBackslash(SourcePath) + "..\dist\Alfred"
#endif
#ifndef BuildDir
  #define BuildDir AddBackslash(SourcePath) + "..\build"
#endif
#ifndef OutputDir
  #define OutputDir AddBackslash(SourcePath) + "..\dist"
#endif

#define AppName "Alfred"
#define AppExe "Alfred.exe"
#define CliExe "alfred-cli.exe"

[Setup]
; Refresh Windows' icon cache at the end, so an update's new icon replaces the old one on shortcuts and the taskbar.
ChangesAssociations=yes
; A fixed id, so a newer installer updates the installed Alfred instead of adding a second one. Never change it.
AppId={{A6BF1F43-FDD9-47FA-AE7F-AAF863E798BF}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Alfred
AppComments=Alfred, the assistant that works the apps on this PC for you.
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
AlwaysShowDirOnReadyPage=yes
; Installs for the current user only. /CURRENTUSER is accepted for scripts; /ALLUSERS is refused below.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsedUserAreasWarning=no
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
; The "licence" page shows Alfred's data notice (PRIVACY.md as text, made by installer.ps1).
LicenseFile={#BuildDir}\PRIVACY.txt
SetupIconFile={#BuildDir}\alfred.ico
WizardStyle=modern
WizardImageFile={#BuildDir}\wizard-large-100.bmp,{#BuildDir}\wizard-large-200.bmp
WizardSmallImageFile={#BuildDir}\wizard-small-100.bmp,{#BuildDir}\wizard-small-200.bmp
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=Alfred-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
; Setup closes a running Alfred itself (PrepareToInstall below), after telling the user.
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] for you on this PC. It needs no administrator rights.%n%nAlfred is an assistant that opens and works the apps on this PC when you ask it to. The next page explains what it can do and where your data goes.
WizardLicense=What Alfred does and where your data goes
LicenseLabel=Please read this notice before you continue.
LicenseLabel3=Alfred also shows this notice the first time it starts, and only runs once you agree there.
LicenseAccepted=I have &read the notice
LicenseNotAccepted=I &do not want to continue
ConfirmUninstall=Remove %1 from this PC? If Alfred is running, it will be closed first.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; The app folder's contents change between versions: start each update from a clean copy.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\node"
Type: filesandordirs; Name: "{app}\telegram"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef WebView2Setup
; Microsoft's Evergreen bootstrapper: only run if the PC lacks the runtime and the user says yes.
Source: "{#WebView2Setup}"; Flags: dontcopy
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "Alfred, your assistant on this PC"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "Alfred, your assistant on this PC"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Anything Alfred's own folders gained after installing (never the user's data, see RemoveUserData).
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\node"
Type: filesandordirs; Name: "{app}\telegram"
Type: dirifempty; Name: "{app}"

[Code]
const
  WebView2Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  WebView2Client = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  { The names Alfred saves in Windows Credential Manager as "Alfred/<NAME>": jarvis/settings.py
    SECRET_KEYS (tests/test_packaging.py checks the two lists match). Only used if alfred-cli.exe can't run. }
  SecretKeys = 'ANTHROPIC_API_KEY,ANTHROPIC_AUTH_TOKEN,OPENAI_API_KEY,TELEGRAM_BOT_TOKEN,SPECTRUM_PROJECT_SECRET,JARVIS_TOKEN,VOICE_SERVICE_TOKEN';

function InitializeSetup(): Boolean;
begin
  Result := True;
  if IsAdminInstallMode then
  begin
    SuppressibleMsgBox('Alfred installs for one Windows user and needs no administrator rights. Run Setup without /ALLUSERS.',
      mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

{ --- Alfred's processes ------------------------------------------------------- }

{ Runs PowerShell over every process started from Dir (the app, its engine and the Telegram bridge's
  node.exe). Action 'check': exit code 3 if any is running, else 0. Action 'stop': ends them, exit code 0.
  Returns PowerShell's exit code, or -1 if PowerShell didn't start. }
function AlfredProcesses(const Dir, Action: String): Integer;
var
  Folder, Script: String;
  Code: Integer;
begin
  Folder := AddBackslash(Dir);
  StringChangeEx(Folder, '''', '''''', True);  { a ' in the path is written '' inside PowerShell's '...' }
  Script := '$d = ''' + Folder + '''; ' +
    '$p = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) }); ';
  if Action = 'stop' then
    Script := Script + '$p | ForEach-Object { Write-Output (''Stopping '' + $_.Path); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }; exit 0'
  else
    Script := Script + 'if ($p.Count -gt 0) { exit 3 } else { exit 0 }';
  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' + Script + '"', '', SW_HIDE,
    ewWaitUntilTerminated, Code) then
    Result := Code
  else
    Result := -1;
end;

procedure StopAlfred(const Dir: String);
var
  Code: Integer;
begin
  Log('Closing Alfred if it is running from ' + Dir);
  if AlfredProcesses(Dir, 'stop') <> 0 then
  begin
    Log('PowerShell could not close Alfred; using taskkill instead.');
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#CliExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
  Sleep(1000);  { let Windows release the files }
end;

{ An update while Alfred runs: say so, then close it, so its files can be replaced. Only 0 from the check
  means "not running": if PowerShell can't run (blocked by AppLocker or WDAC on managed PCs, for example),
  Alfred may still be running, and StopAlfred falls back to taskkill. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Running: Integer;
  Notice: String;
begin
  Result := '';
  if not FileExists(ExpandConstant('{app}\{#AppExe}')) then
    Exit;
  Running := AlfredProcesses(ExpandConstant('{app}'), 'check');
  if Running = 0 then
    Exit;
  if Running = 3 then
    Notice := 'Alfred is running. Setup will close it, and stop anything it is doing, to update it.'
  else
  begin
    Log('Could not check whether Alfred is running (result ' + IntToStr(Running) + '); closing it in case it is.');
    Notice := 'Setup will close Alfred if it is running, and stop anything it is doing, to update it.';
  end;
  if not WizardSilent then
  begin
    if MsgBox(Notice, mbInformation, MB_OKCANCEL) <> IDOK then
    begin
      Result := 'Setup did not close Alfred. Quit it from its tray icon, then run Setup again.';
      Exit;
    end;
  end;
  StopAlfred(ExpandConstant('{app}'));
end;

{ --- Edge WebView2 Runtime (Alfred's window) ----------------------------------- }

function IsRealVersion(const Version: String): Boolean;
begin
  Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result := True;
  Version := '';
  if RegQueryStringValue(HKLM32, 'SOFTWARE\' + WebView2Client, 'pv', Version) then
    if IsRealVersion(Version) then
      Exit;
  Version := '';
  if RegQueryStringValue(HKLM64, 'SOFTWARE\' + WebView2Client, 'pv', Version) then
    if IsRealVersion(Version) then
      Exit;
  Version := '';
  if RegQueryStringValue(HKCU, 'Software\' + WebView2Client, 'pv', Version) then
    if IsRealVersion(Version) then
      Exit;
  Result := False;
end;

procedure OfferWebView2();
var
  Code: Integer;
begin
  if WebView2Installed() then
  begin
    Log('Edge WebView2 Runtime found.');
    Exit;
  end;
  if WizardSilent then
  begin
    Log('Edge WebView2 Runtime missing; not offered in a silent install. Alfred opens in the browser until it is installed.');
    Exit;
  end;
#ifdef WebView2Setup
  if MsgBox('Alfred''s window uses the Microsoft Edge WebView2 Runtime, which isn''t on this PC yet.' + #13#10#13#10 +
    'Install it now? Microsoft''s installer downloads it from Microsoft.' + #13#10 +
    'Without it, Alfred opens in your web browser instead.', mbConfirmation, MB_YESNO) <> IDYES then
  begin
    Log('The user chose not to install the WebView2 Runtime.');
    Exit;
  end;
  ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  if Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/install', '', SW_SHOW, ewWaitUntilTerminated, Code) then
    Log('WebView2 installer exit code ' + IntToStr(Code))
  else
    Log('The WebView2 installer did not start: ' + SysErrorMessage(Code));
  if not WebView2Installed() then
    MsgBox('The WebView2 Runtime isn''t installed, so Alfred opens in your web browser for now.' + #13#10 +
      'You can install it later from ' + WebView2Url, mbInformation, MB_OK);
#else
  MsgBox('Alfred''s window uses the Microsoft Edge WebView2 Runtime, which isn''t on this PC yet, so Alfred opens in your web browser for now.' + #13#10#13#10 +
    'For Alfred''s own window, install the runtime from ' + WebView2Url, mbInformation, MB_OK);
#endif
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    OfferWebView2();
end;

{ --- Uninstall ------------------------------------------------------------------- }

function ParamGiven(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

{ Only when the user asked: Alfred's settings, saved keys and logs (%LOCALAPPDATA%\Jarvis and the
  "Alfred/<NAME>" credentials). alfred-cli.exe does it (jarvis/uninstall.py); if it can't run, this does. }
procedure RemoveUserData();
var
  Code, Comma: Integer;
  Cli, Keys, Key: String;
begin
  Cli := ExpandConstant('{app}\{#CliExe}');
  if FileExists(Cli) then
    if Exec(Cli, '--remove-user-data --yes', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      if Code = 0 then
      begin
        Log('Removed Alfred''s settings, saved keys and logs.');
        Exit;
      end;
  Log('alfred-cli.exe --remove-user-data did not finish; removing the data folder and the known keys directly.');
  DelTree(ExpandConstant('{localappdata}\Jarvis'), True, True, True);
  Keys := SecretKeys + ',';
  while Keys <> '' do
  begin
    Comma := Pos(',', Keys);
    Key := Trim(Copy(Keys, 1, Comma - 1));
    Delete(Keys, 1, Comma);
    if Key <> '' then
      Exec(ExpandConstant('{sys}\cmdkey.exe'), '/delete:Alfred/' + Key, '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Remove: Boolean;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  StopAlfred(ExpandConstant('{app}'));
  if UninstallSilent then
    Remove := ParamGiven('/REMOVEUSERDATA')
  else
    { No is the default: settings and keys stay unless the user picks Yes. }
    Remove := MsgBox('Also remove your Alfred settings, saved API keys and activity log from this PC?' + #13#10#13#10 +
      'Choose No to keep them, for example if you will install Alfred again.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  if Remove then
    RemoveUserData()
  else
    Log('Keeping Alfred''s settings, saved keys and logs.');
end;
