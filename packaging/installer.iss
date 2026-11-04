; WangDoodletheGIS Windows installer (Inno Setup 6.1+)
;
; Two editions from this one script:
;   ISCC /DBUNDLE_ENGINE installer.iss       -> WangDoodletheGIS_Setup_<v>_Offline.exe  (engine inside; no internet)
;   ISCC /DODM_URL=... /DODM_SHA256=... ...  -> WangDoodletheGIS_Setup_<v>_Online.exe   (downloads the engine)
;
; Expects:
;   ..\dist\WangDoodletheGIS\      PyInstaller output (app + GIS libraries + EGM96 geoid grid)
;   redist\ODM_Setup.exe           official OpenDroneMap Windows installer (offline edition)
; Silent install (IT / scripted):  WangDoodletheGIS_Setup_x.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

#define MyAppName "WangDoodletheGIS"
#ifndef MyAppVersion
  #define MyAppVersion "0.2.2"
#endif
#define MyAppExe "WangDoodletheGIS.exe"
#define MyCliExe "WangDoodletheGIS-cli.exe"
#ifndef ODM_URL
  #define ODM_URL ""
#endif
#ifndef ODM_SHA256
  #define ODM_SHA256 ""
#endif
#ifndef ODM_VERSION
  #define ODM_VERSION "latest"
#endif
#ifdef BUNDLE_ENGINE
  #define Edition "Offline"
#else
  #define Edition "Online"
#endif

[Setup]
AppId={{3F8A2C61-9D4E-4B7A-A1C5-7E2B9D0F6A38}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Migz
AppComments=Offline drone mapping, map georeferencing and flood/landslide scenarios
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=WangDoodletheGIS_Setup_{#MyAppVersion}_{#Edition}
SetupIconFile=..\fieldortho\resources\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
DiskSpanning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
WizardStyle=modern
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE-NOTICE.txt
ChangesEnvironment=no
CloseApplications=yes

[Types]
Name: "full"; Description: "{#MyAppName} with the OpenDroneMap processing engine (recommended)"
Name: "apponly"; Description: "{#MyAppName} only (georeferencing, modelling, exports; engine installed later)"

[Components]
Name: "app"; Description: "{#MyAppName} application (with GIS libraries and EGM96 geoid)"; Types: full apponly; Flags: fixed
#ifdef BUNDLE_ENGINE
Name: "engine"; Description: "OpenDroneMap {#ODM_VERSION} processing engine (installs to C:\ODM)"; Types: full
#else
Name: "engine"; Description: "OpenDroneMap {#ODM_VERSION} processing engine - downloaded during setup (about 1-2 GB, internet needed)"; Types: full
#endif

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "jobsfolder"; Description: "Create the '{#MyAppName} Jobs' folder in Documents"; GroupDescription: "Folders:"

[Files]
Source: "..\dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: app
Source: "..\LICENSE-NOTICE.txt"; DestDir: "{app}"; Flags: ignoreversion; Components: app
; AI point-classification model ODM would otherwise download during processing (offline laptops)
Source: "redist\openpointclass\model.bin"; DestDir: "{commonappdata}\ODM\storage\models\openpointclass\v1_0_0"; Flags: onlyifdoesntexist uninsneveruninstall; Components: engine
#ifdef BUNDLE_ENGINE
Source: "redist\ODM_Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Components: engine
#endif

[Dirs]
Name: "{userdocs}\{#MyAppName} Jobs"; Tasks: jobsfolder

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\{#MyAppName} command line"; Filename: "{cmd}"; Parameters: "/k ""{app}\{#MyCliExe}"" --help"; WorkingDir: "{userdocs}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
; OpenDroneMap's own installer (also Inno Setup) - silent, into C:\ODM, installs its VC++ runtime
Filename: "{tmp}\ODM_Setup.exe"; Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=""C:\ODM"" /TASKS="""""; \
  StatusMsg: "Installing the OpenDroneMap processing engine (this takes a few minutes)..."; \
  Components: engine; Check: EngineSetupReady; Flags: waituntilterminated
Filename: "{app}\{#MyAppExe}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "https://qgis.org/download/"; Description: "Open the QGIS download page (free GIS to view the results)"; Flags: shellexec postinstall skipifsilent unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
var
  DownloadPage: TDownloadWizardPage;
  EngineDownloaded: Boolean;

function EngineInstalled(): Boolean;
begin
  Result := FileExists('C:\ODM\run.bat');
end;

function EngineSetupReady(): Boolean;
begin
  { run the engine setup only if we have it and the engine is not already there }
  Result := FileExists(ExpandConstant('{tmp}\ODM_Setup.exe')) and not EngineInstalled();
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), 'Downloading the OpenDroneMap engine...', @OnDownloadProgress);
  EngineDownloaded := False;
end;

function NeedDownload(): Boolean;
begin
#ifdef BUNDLE_ENGINE
  Result := False;
#else
  Result := WizardIsComponentSelected('engine') and (not EngineInstalled()) and ('{#ODM_URL}' <> '') and (not EngineDownloaded);
#endif
end;

function DownloadEngine(Silent: Boolean): String;
begin
  Result := '';
  if not NeedDownload() then Exit;
  try
    if Silent then
      DownloadTemporaryFile('{#ODM_URL}', 'ODM_Setup.exe', '{#ODM_SHA256}', @OnDownloadProgress)
    else begin
      DownloadPage.Clear;
      DownloadPage.Add('{#ODM_URL}', 'ODM_Setup.exe', '{#ODM_SHA256}');
      DownloadPage.Show;
      try
        DownloadPage.Download;
      finally
        DownloadPage.Hide;
      end;
    end;
    EngineDownloaded := True;
  except
    Result := 'The processing engine could not be downloaded (' + GetExceptionMessage + '). ' +
              '{#MyAppName} will still be installed; later use Settings > "Download and install engine".';
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Msg: String;
begin
  Result := True;
  if CurPageID = wpReady then begin
    Msg := DownloadEngine(False);
    if Msg <> '' then
      Result := SuppressibleMsgBox(Msg + #13#10#13#10 + 'Continue without the engine?', mbConfirmation, MB_YESNO, IDYES) = IDYES;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  { silent installs never click Next: download here instead (failure is not fatal) }
  if WizardSilent() then
    DownloadEngine(True);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWin64 then begin
    MsgBox('{#MyAppName} needs 64-bit Windows 10 or 11.', mbError, MB_OK);
    Result := False;
  end;
end;
