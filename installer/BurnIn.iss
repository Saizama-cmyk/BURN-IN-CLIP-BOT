; BURN-IN installer (Inno Setup 6, free). Build with installer\build_installer.bat.
; Per-user install (no admin), BURN-IN as publisher everywhere Windows shows it, optional
; one-click setup of the free prerequisites (ffmpeg, Ollama) through winget.

#define AppVer GetEnv("CLIPBOT_VERSION")
#if AppVer == ""
  #define AppVer "1.0.0"
#endif

[Setup]
AppId={{6C1F2B4E-5A0D-4F7B-9C3E-5D0B7A51C0B7}
AppName=BURN-IN
AppVersion={#AppVer}
AppVerName=BURN-IN {#AppVer}
AppPublisher=BURN-IN
AppCopyright=(c) BURN-IN
VersionInfoCompany=BURN-IN
VersionInfoProductName=BURN-IN
VersionInfoDescription=BURN-IN Setup
VersionInfoVersion={#AppVer}
UninstallDisplayName=BURN-IN
UninstallDisplayIcon={app}\BurnIn.exe
DefaultDirName={localappdata}\Programs\BURN-IN\BURN-IN
UsePreviousAppDir=yes
DefaultGroupName=BURN-IN
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=BURN-IN-Setup-{#AppVer}
SetupIconFile=..\assets\clipbot.ico
WizardStyle=modern
LicenseFile=..\TERMS.md
WizardImageFile=wizard.bmp
WizardSmallImageFile=wizard_small.bmp
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
AppMutex=Local\ClipBot.SingleInstance
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Messages]
WelcomeLabel1=Welcome to BURN-IN
WelcomeLabel2=BURN-IN watches the biggest live streams, catches the moments chat loses it over, cuts and captions them, and posts them for you.%n%nThis installs BURN-IN {#AppVer} for your Windows account. Your settings, clips and keys are kept between updates.

[Tasks]
Name: "desktopicon"; Description: "Put a BURN-IN shortcut on the desktop"; GroupDescription: "Shortcuts:"
Name: "ffmpeg"; Description: "Install ffmpeg (required, free) with winget"; GroupDescription: "Set up what BURN-IN needs:"; Check: NeedsFfmpeg
Name: "ollama"; Description: "Install Ollama (required for the AI, free) with winget"; GroupDescription: "Set up what BURN-IN needs:"; Check: NeedsOllama

[InstallDelete]
; the older install.bat layout
Type: files; Name: "{userprograms}\ClipBot.lnk"
Type: files; Name: "{userprograms}\Uninstall ClipBot.lnk"
Type: files; Name: "{userdesktop}\ClipBot.lnk"
; the first BURN-IN build (ClipBot name)
Type: files; Name: "{group}\ClipBot.lnk"
Type: files; Name: "{group}\Uninstall ClipBot.lnk"

[Files]
Source: "..\dist\BurnIn\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\BURN-IN"; Filename: "{app}\BurnIn.exe"; Comment: "BURN-IN"
Name: "{group}\Uninstall BURN-IN"; Filename: "{uninstallexe}"
Name: "{userdesktop}\BURN-IN"; Filename: "{app}\BurnIn.exe"; Comment: "BURN-IN"; Tasks: desktopicon

[Registry]
; "Start with Windows" is written by the app itself; only make sure uninstall removes it
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "ClipBot"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "winget"; Parameters: "install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements --silent"; StatusMsg: "Installing ffmpeg…"; Flags: runhidden waituntilterminated; Tasks: ffmpeg
Filename: "winget"; Parameters: "install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements --silent"; StatusMsg: "Installing Ollama…"; Flags: runhidden waituntilterminated; Tasks: ollama
Filename: "{app}\BurnIn.exe"; Description: "Start BURN-IN"; Flags: nowait postinstall skipifsilent
Filename: "{app}\BurnIn.exe"; Flags: nowait; Check: WizardSilent

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

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Data: String;
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then begin
    Data := ExpandConstant('{localappdata}\ClipBot');
    if DirExists(Data) then
      if MsgBox('Also delete your BURN-IN data (settings, keys, clips and database) in ' + Data + '?',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
