; BURN-IN web installer: a ~2 MB setup you hand out once. It always downloads the newest
; full installer from GitHub Releases (…/releases/latest/download/BURN-IN-Setup.exe) and
; runs it, so new versions never need a new web installer. Built by installer\build_installer.bat.

#define Repo GetEnv("CLIPBOT_REPO")
#if Repo == ""
  #error Set CLIPBOT_REPO=owner/name (the GitHub repo that publishes releases) before building.
#endif
#define Asset "BURN-IN-Setup.exe"

[Setup]
AppId={{6C1F2B4E-5A0D-4F7B-9C3E-5D0B7A51C0B8}
AppName=BURN-IN
AppVersion=latest
AppPublisher=BURN-IN
VersionInfoCompany=BURN-IN
VersionInfoProductName=BURN-IN
VersionInfoDescription=BURN-IN Web Setup
CreateAppDir=no
Uninstallable=no
DisableWelcomePage=no
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=BURN-IN-WebSetup
SetupIconFile=..\assets\clipbot.ico
WizardStyle=modern
WizardImageFile=wizard.bmp
WizardSmallImageFile=wizard_small.bmp

[Messages]
WelcomeLabel1=BURN-IN
WelcomeLabel2=This downloads the latest version of BURN-IN (about 1 GB) and installs it for your Windows account. It can also set up ffmpeg and Ollama, the free tools it needs.

[Code]
var
  DownloadPage: TDownloadWizardPage;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), 'Downloading the latest BURN-IN…', nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Code: Integer;
begin
  Result := True;
  if CurPageID = wpWelcome then begin
    DownloadPage.Clear;
    DownloadPage.Add('https://github.com/{#Repo}/releases/latest/download/{#Asset}', '{#Asset}', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
      except
        SuppressibleMsgBox('Could not download BURN-IN: ' + AddPeriod(GetExceptionMessage) +
          #13#10#13#10 + 'Check your internet connection and try again.', mbCriticalError, MB_OK, IDOK);
        Result := False;
        Exit;
      end;
    finally
      DownloadPage.Hide;
    end;
    // Keep the download in this setup's private temporary directory until it exits.
    if not Exec(ExpandConstant('{tmp}\{#Asset}'), '', '', SW_SHOW, ewWaitUntilTerminated, Code) then begin
      SuppressibleMsgBox('Could not start the BURN-IN installer (' + SysErrorMessage(Code) + ').', mbCriticalError, MB_OK, IDOK);
      Result := False;
      Exit;
    end;
    if (Code <> 0) and (Code <> 3010) then begin
      SuppressibleMsgBox('BURN-IN installation did not finish (exit code ' + IntToStr(Code) +
        '). You can try again.', mbError, MB_OK, IDOK);
      Result := False;
      Exit;
    end;
    WizardForm.Close;
    Result := False;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  Confirm := False;
end;
