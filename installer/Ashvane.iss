; Ashvane installer (Inno Setup 6, free). Build with installer\build_installer.bat.
; Per-user install (no admin), Ashvane as publisher everywhere Windows shows it, optional
; one-click setup of the free prerequisites (ffmpeg, Ollama) through winget.
;
; The app was called BURN-IN before. The AppId is unchanged, so this upgrades that install in
; place of adding a second one: it moves to the new folder, and the old program folder,
; shortcuts and autostart entry are removed. Your data is moved by the app on its first start.

#define AppVer GetEnv("CLIPBOT_VERSION")
#if AppVer == ""
  #define AppVer "1.0.0"
#endif
#define AppName "Ashvane"
#define AppExe "Ashvane.exe"

[Setup]
AppId={{6C1F2B4E-5A0D-4F7B-9C3E-5D0B7A51C0B7}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher={#AppName}
AppCopyright=(c) {#AppName}
VersionInfoCompany={#AppName}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
VersionInfoVersion={#AppVer}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
DefaultDirName={localappdata}\Programs\{#AppName}
; always the new folder, even when upgrading an install that lives under the old name
UsePreviousAppDir=no
DefaultGroupName={#AppName}
; and the new Start menu folder, not the one the old install used
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename={#AppName}-Setup-{#AppVer}
SetupIconFile=..\assets\clipbot.ico
WizardStyle=modern
LicenseFile=..\TERMS.md
WizardImageFile=wizard.bmp
WizardSmallImageFile=wizard_small.bmp
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
; the current app and any copy from before the rename
AppMutex=Local\Ashvane.SingleInstance,Local\ClipBot.SingleInstance
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Messages]
WelcomeLabel1=Welcome to {#AppName}
WelcomeLabel2={#AppName} watches the biggest live streams, catches the moments chat loses it over, cuts and captions them, and posts them for you.%n%nThis installs {#AppName} {#AppVer} for your Windows account. Your settings, clips and keys are kept between updates.

[Tasks]
Name: "desktopicon"; Description: "Put an {#AppName} shortcut on the desktop"; GroupDescription: "Shortcuts:"
Name: "ffmpeg"; Description: "Install ffmpeg (required, free) with winget"; GroupDescription: "Set up what {#AppName} needs:"; Check: NeedsFfmpeg
Name: "ollama"; Description: "Install Ollama (required for the AI, free) with winget"; GroupDescription: "Set up what {#AppName} needs:"; Check: NeedsOllama
Name: "firewall"; Description: "Let your phone reach {#AppName} on your home network (port 8787)"; GroupDescription: "Set up what {#AppName} needs:"; Flags: unchecked

[InstallDelete]
; shortcuts from the older names
Type: files; Name: "{userprograms}\ClipBot.lnk"
Type: files; Name: "{userprograms}\Uninstall ClipBot.lnk"
Type: files; Name: "{userdesktop}\ClipBot.lnk"
Type: filesandordirs; Name: "{userprograms}\BURN-IN"
Type: files; Name: "{userdesktop}\BURN-IN.lnk"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "allow-phone-remote.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "{#AppName}"; AppUserModelID: "Ashvane.App"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "{#AppName}"; AppUserModelID: "Ashvane.App"; Tasks: desktopicon

[Registry]
; "Start with Windows" is written by the app itself; only make sure uninstall removes it
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "{#AppName}"; Flags: uninsdeletevalue dontcreatekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "ClipBot"; Flags: deletevalue uninsdeletevalue dontcreatekey

[Run]
Filename: "winget"; Parameters: "install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements --silent"; StatusMsg: "Installing ffmpeg…"; Flags: runhidden waituntilterminated; Tasks: ffmpeg
Filename: "winget"; Parameters: "install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements --silent"; StatusMsg: "Installing Ollama…"; Flags: runhidden waituntilterminated; Tasks: ollama
; the firewall rule needs administrator rights, so this one asks for them on its own
Filename: "{app}\allow-phone-remote.cmd"; Parameters: "/quiet"; StatusMsg: "Allowing the phone remote through the firewall..."; Flags: shellexec runhidden waituntilterminated; Tasks: firewall
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[Code]
function OnPath(const Exe: String): Boolean;
var Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/C where ' + Exe + ' >nul 2>nul', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function NeedsFfmpeg: Boolean;
begin
  Result := not OnPath('ffmpeg');
end;

function NeedsOllama: Boolean;
begin
  Result := not (OnPath('ollama') or FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe')));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Old: String;
begin
  { The program folder from before the rename holds only program files (your data lives
    elsewhere), and the new install replaces its uninstall entry, so it can go. }
  if CurStep = ssPostInstall then begin
    Old := ExpandConstant('{localappdata}\Programs\BURN-IN');
    if DirExists(Old) and (CompareText(Old, ExpandConstant('{app}')) <> 0) then
      DelTree(Old, True, True, True);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Data: String;
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then begin
    Data := ExpandConstant('{localappdata}\{#AppName}');
    if not DirExists(Data) then
      Data := ExpandConstant('{localappdata}\ClipBot');
    if DirExists(Data) then
      if MsgBox('Also delete your {#AppName} data (settings, keys, clips and database) in ' + Data + '?',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
