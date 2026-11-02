; Compile through tools/build-installer.ps1. The installer is per-user; only
; the driver helper is elevated. Never use the legacy global --stop command.
#ifndef AppVersion
  #define AppVersion "0.4.5"
#endif
#ifndef PublishDirectory
  #error PublishDirectory is required
#endif
#ifndef DependencyDirectory
  #error DependencyDirectory is required
#endif
#ifndef OutputDirectory
  #define OutputDirectory "..\dist"
#endif
#ifdef InstallerTestBuild
  #define ProductName "MadSense Installer Test"
  #define ProductId "MadSense.Installer.Test"
  #define ProductDirectory "MadSense-Installer-Test"
  #define OutputName "MadSense-" + AppVersion + "-Setup-TEST"
#else
  #define ProductName "MadSense"
  #define ProductId "MadSense.Windows"
  #define ProductDirectory "MadSense"
  #define OutputName "MadSense-" + AppVersion + "-Setup"
#endif

[Setup]
AppId={#ProductId}
AppName={#ProductName}
AppVersion={#AppVersion}
AppPublisher=Alex García Martín
AppPublisherURL=https://madsense.pro/
AppSupportURL=https://github.com/AlexGarciaMartin/MadSense/issues
AppUpdatesURL=https://github.com/AlexGarciaMartin/MadSense/releases
DefaultDirName={localappdata}\Programs\{#ProductDirectory}
DefaultGroupName={#ProductName}
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19045
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=yes
WizardStyle=modern light
WizardSizePercent=125
WizardBackColor=#f0ebdc
WizardImageFile=assets\wizard-large.bmp,assets\wizard-large@2x.bmp,assets\wizard-large@3x.bmp
WizardSmallImageFile=assets\wizard-small.bmp,assets\wizard-small@2x.bmp,assets\wizard-small@3x.bmp
WizardImageStretch=yes
SetupIconFile=assets\installer.ico
UninstallDisplayIcon={app}\MadSense.exe
UninstallDisplayName={#ProductName}
UninstallFilesDir={app}\uninstall
OutputDir={#OutputDirectory}
OutputBaseFilename={#OutputName}
Compression=lzma2/normal
SolidCompression=no
CloseApplications=no
RestartApplications=no
ChangesEnvironment=no
SetupLogging=yes
AllowNoIcons=no
VersionInfoVersion={#AppVersion}
VersionInfoDescription=MadSense Setup
VersionInfoProductName={#ProductName}
VersionInfoCompany=Alex García Martín
ShowLanguageDialog=yes
LanguageDetectionMethod=uilanguage
UsePreviousLanguage=yes

[Languages]
; English is the fallback for Windows languages not listed here.
Name: "english"; MessagesFile: "compiler:Default.isl,languages\english.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl,languages\spanish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl,languages\french.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl,languages\german.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl,languages\italian.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl,languages\brazilianportuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; Flags: unchecked

[Files]
; Support comes first so it can be read without decompressing the entire app.
#ifdef InstallerTestBuild
Source: "..\tests\installer\FakeDriverSetup.ps1"; DestDir: "{tmp}\support"; DestName: "DriverSetup.ps1"; Flags: dontcopy
#else
Source: "DriverSetup.ps1"; DestDir: "{tmp}\support"; Flags: dontcopy
#endif
Source: "DriverSetup.Core.psm1"; DestDir: "{tmp}\support"; Flags: dontcopy
Source: "DriverSetup.Messages.psd1"; DestDir: "{tmp}\support"; Flags: dontcopy
Source: "dependencies.json"; DestDir: "{tmp}\support"; Flags: dontcopy
Source: "assets\localized\*.bmp"; DestDir: "{tmp}\art"; Flags: dontcopy
Source: "{#DependencyDirectory}\*.exe"; DestDir: "{tmp}\drivers"; Flags: dontcopy
; The temporary copy provides maintenance commands even when upgrading 0.4.
; Inno reuses duplicate source data in the archive.
Source: "{#PublishDirectory}\*"; DestDir: "{tmp}\payload"; Flags: dontcopy recursesubdirs
Source: "{#PublishDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Seed only: an explicit preference (including automatic) always takes priority.
Source: "app-languages\english.txt"; DestDir: "{app}"; DestName: "installer-language.txt"; Languages: "english"; Flags: ignoreversion
Source: "app-languages\spanish.txt"; DestDir: "{app}"; DestName: "installer-language.txt"; Languages: "spanish"; Flags: ignoreversion
Source: "app-languages\french.txt"; DestDir: "{app}"; DestName: "installer-language.txt"; Languages: "french"; Flags: ignoreversion
Source: "app-languages\german.txt"; DestDir: "{app}"; DestName: "installer-language.txt"; Languages: "german"; Flags: ignoreversion
Source: "app-languages\italian.txt"; DestDir: "{app}"; DestName: "installer-language.txt"; Languages: "italian"; Flags: ignoreversion
Source: "app-languages\brazilianportuguese.txt"; DestDir: "{app}"; DestName: "installer-language.txt"; Languages: "brazilianportuguese"; Flags: ignoreversion
#ifndef InstallerTestBuild
Source: "DriverSetup.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
#endif
Source: "DriverSetup.Core.psm1"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "DriverSetup.Messages.psd1"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "dependencies.json"; DestDir: "{app}\setup"; Flags: ignoreversion
#ifdef InstallerTestBuild
Source: "..\tests\installer\FakeDriverSetup.ps1"; DestDir: "{app}\setup"; DestName: "DriverSetup.ps1"; Flags: ignoreversion
#endif
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dependency-notices.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#ProductName}"; Filename: "{app}\MadSense.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\{#ProductName}"; Filename: "{app}\MadSense.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\MadSense.exe"; Description: "{cm:OpenApp}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser; Check: CanLaunchMadSense

[Code]
var
  DriversRestart, HelpersExtracted, PayloadExtracted, DriverPrepared: Boolean;
  RegistrationPending, RegistrationFailed, OtherInstance, LaunchChecked: Boolean;
  LastHelperError, RegistrationError: String;
  StageLabel: TNewStaticText;
  DesktopChoice: TNewCheckBox;
  HeaderMinHeight, HeaderBodyGap, HeaderBodyBottom, HeaderSeparatorGap: Integer;

procedure LoadLocalizedArtwork;
var ArtName: String;
begin
  // A 3x bitmap stays sharp at high DPI; both welcome and finished pages use it.
  ArtName := 'sidebar-' + ActiveLanguage + '.bmp';
  ExtractTemporaryFiles('{tmp}\art\' + ArtName);
  WizardForm.WizardBitmapImage.Bitmap.LoadFromFile(ExpandConstant('{tmp}\art\') + ArtName);
  WizardForm.WizardBitmapImage2.Bitmap.Assign(WizardForm.WizardBitmapImage.Bitmap);
end;

procedure FitPageHeader(Title, Description: TNewStaticText; Header: TPanel;
  Separator: TBevel; Body: TNewNotebook; MinHeight, BodyGap, BodyBottom, SeparatorGap: Integer);
var RequiredHeight, HeaderBottom: Integer;
begin
  // Font changes do not resize Inno's fixed-height title controls. Measure the
  // current caption at the current DPI, then move the whole header/body boundary.
  Title.AdjustHeight;
  Title.Height := Title.Height + ScaleY(2);
  Description.Top := Title.Top + Title.Height + ScaleY(5);
  Description.AdjustHeight;
  RequiredHeight := Description.Top + Description.Height + ScaleY(12);
  if RequiredHeight < MinHeight then RequiredHeight := MinHeight;
  Header.Height := RequiredHeight;
  HeaderBottom := Header.Top + Header.Height;
  Separator.Top := HeaderBottom + SeparatorGap;
  Body.Top := HeaderBottom + BodyGap;
  Body.Height := BodyBottom - Body.Top;
end;

function FitSideText(Title, Body: TNewStaticText; MinBodyTop: Integer): Integer;
begin
  Title.AdjustHeight;
  Title.Height := Title.Height + ScaleY(4);
  Body.Top := Title.Top + Title.Height + ScaleY(18);
  if Body.Top < MinBodyTop then Body.Top := MinBodyTop;
  Body.AdjustHeight;
  Body.Height := Body.Height + ScaleY(2);
  Result := Body.Top + Body.Height + ScaleY(20);
  if Result < ScaleY(267) then Result := ScaleY(267);
end;

function Quote(const Value: String): String;
begin
  Result := '"' + Value + '"';
end;

function PowerShellPath: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

procedure ExtractHelpers;
begin
  if HelpersExtracted then Exit;
  ExtractTemporaryFiles('{tmp}\support\*');
  HelpersExtracted := True;
end;

procedure ExtractPayload;
begin
  if PayloadExtracted then Exit;
  ExtractTemporaryFiles('{tmp}\payload\*');
  PayloadExtracted := True;
end;

function Helper(const Action, ScriptPath, ResultFile: String; Elevated, AllowPending: Boolean): Boolean;
var Params, ErrorText: String; ExitCode: Integer; Started: Boolean;
begin
  LastHelperError := '';
  DeleteFile(ResultFile);
  Params := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + Quote(ScriptPath) +
    ' -Action ' + Action + ' -ResultPath ' + Quote(ResultFile) +
    ' -AppPath ' + Quote(ExpandConstant('{app}\MadSense.exe')) +
    ' -Language ' + Quote(ActiveLanguage);
  if Action = 'Install' then Params := Params + ' -PayloadDirectory ' + Quote(ExpandConstant('{tmp}\drivers'));
  if AllowPending then Params := Params + ' -AllowPendingReboot';
#ifdef InstallerTestBuild
  Params := Params + ' -Scenario ' + Quote(ExpandConstant('{param:TESTSCENARIO|normal}'));
  Elevated := False;
#endif
  Log('Driver helper: ' + Action);
  if Elevated then
    Started := ShellExec('runas', PowerShellPath, Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode)
  else
    // Preserve early PowerShell/module diagnostics as well as the result INI.
    Started := ExecAndLogOutput(PowerShellPath, Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode, nil);
  Result := Started and ((ExitCode = 0) or (ExitCode = 3010)) and
    (GetIniString('Result', 'Success', '0', ResultFile) = '1');
  // A partial driver installation may still require a restart.
  if (ExitCode = 3010) or (GetIniString('Result', 'RebootRequired', '0', ResultFile) = '1') then DriversRestart := True;
  if not Result then begin
    ErrorText := GetIniString('Result', 'Error', '', ResultFile);
    if ErrorText = '' then ErrorText := FmtMessage(CustomMessage('StepFailed'), [IntToStr(ExitCode)]);
    LastHelperError := ErrorText;
    Log('Driver helper failed: ' + ErrorText);
  end else begin
    RegistrationPending := GetIniString('Result', 'RegistrationPending', '0', ResultFile) = '1';
  end;
end;

function MaintenanceError(const ResultFile: String; ExitCode: Integer): String;
var Lines: TArrayOfString; I: Integer; Diagnostic: String;
begin
  Diagnostic := '';
  if LoadStringsFromFile(ResultFile, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do begin
      if Diagnostic <> '' then Diagnostic := Diagnostic + #13#10;
      Diagnostic := Diagnostic + Lines[I];
    end;
  // Keep raw app diagnostics in the log. The app protocol uses stable exit codes,
  // so installer guidance does not depend on translating arbitrary exception text.
  Log('Maintenance failed (' + IntToStr(ExitCode) + '): ' + Diagnostic);
  Result := FmtMessage(CustomMessage('MaintenanceFailed'), [IntToStr(ExitCode)]);
  if ExitCode = 2 then Result := Result + #13#10#13#10 + CustomMessage('MaintenanceBusy');
  if ExitCode = 3 then Result := Result + #13#10#13#10 + CustomMessage('MaintenanceRecovery');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var ResultFile, MaintenanceResult: String; ExitCode: Integer;
begin
  Result := '';
  ExtractHelpers;
  ExtractPayload;
  MaintenanceResult := ExpandConstant('{tmp}\maintenance.txt');
  DeleteFile(MaintenanceResult);
  ExitCode := -1;
  if not Exec(ExpandConstant('{tmp}\payload\MadSense.exe'), '--prepare-update --install-path ' +
    Quote(ExpandConstant('{app}\MadSense.exe')) + ' --result-path ' + Quote(MaintenanceResult),
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then begin
    Result := MaintenanceError(MaintenanceResult, ExitCode);
    Exit;
  end;
  if DriverPrepared then Exit;
  ResultFile := ExpandConstant('{tmp}\driver-result.ini');
  WizardForm.StatusLabel.Caption := CustomMessage('CheckingDrivers');
  if not Helper('Inspect', ExpandConstant('{tmp}\support\DriverSetup.ps1'), ResultFile, False, False) then begin
    Result := LastHelperError;
    Exit;
  end;
  if GetIniString('Result', 'NeedsInstall', '1', ResultFile) = '1' then begin
    WizardForm.StatusLabel.Caption := CustomMessage('InstallingDrivers');
    ExtractTemporaryFiles('{tmp}\drivers\*');
    if not Helper('Install', ExpandConstant('{tmp}\support\DriverSetup.ps1'), ResultFile, True, False) then begin
      Result := LastHelperError;
      NeedsRestart := DriversRestart;
      if DriversRestart then Result := Result + ' ' + CustomMessage('RestartRetry');
      Exit;
    end;
  end;
  DriverPrepared := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var ResultFile: String;
begin
  if CurStep = ssPostInstall then begin
    ResultFile := ExpandConstant('{tmp}\register-result.ini');
    RegistrationFailed := not Helper('Register', ExpandConstant('{app}\setup\DriverSetup.ps1'), ResultFile, False, DriversRestart);
    if RegistrationFailed then begin
      RegistrationError := LastHelperError;
      Log('INSTALLATION INCOMPLETE: ' + RegistrationError);
    end;
    if RegistrationPending then Log('HidHide registration deferred until requested restart; application rechecks on startup.');
  end;
end;

function NeedRestart: Boolean;
begin
  Result := DriversRestart and not RegistrationFailed;
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if RegistrationFailed then Result := 1;
end;

procedure CurPageChanged(CurPageID: Integer);
var ActionsTop: Integer;
begin
  if (CurPageID <> wpWelcome) and (CurPageID <> wpFinished) then
    FitPageHeader(WizardForm.PageNameLabel, WizardForm.PageDescriptionLabel,
      WizardForm.MainPanel, WizardForm.Bevel1, WizardForm.InnerNotebook,
      HeaderMinHeight, HeaderBodyGap, HeaderBodyBottom, HeaderSeparatorGap);
  if CurPageID = wpWelcome then begin
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
    DesktopChoice.Top := FitSideText(WizardForm.WelcomeLabel1, WizardForm.WelcomeLabel2, ScaleY(136));
  end;
  if (CurPageID = wpFinished) and DriversRestart and not RegistrationFailed then begin
    WizardForm.FinishedHeadingLabel.Caption := CustomMessage('RestartHeading');
    // /NORESTART suppresses Inno's restart controls and its restart body text.
    // The driver requirement still applies even when the user will reboot later.
    WizardForm.FinishedLabel.Caption := SetupMessage(msgFinishedRestartLabel);
    WizardForm.NoRadio.Checked := True;
  end;
  if (CurPageID = wpFinished) and RegistrationFailed then begin
    WizardForm.FinishedHeadingLabel.Caption := CustomMessage('IncompleteHeading');
    WizardForm.FinishedLabel.Caption := RegistrationError + #13#10#13#10 +
      CustomMessage('RepairInstall');
    if DriversRestart then WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10 + CustomMessage('RestartFirst');
  end;
  if CurPageID = wpFinished then begin
    // Measure translated captions after Inno has populated the final page.
    ActionsTop := FitSideText(WizardForm.FinishedHeadingLabel, WizardForm.FinishedLabel, ScaleY(116));
    WizardForm.RunList.Top := ActionsTop;
    WizardForm.YesRadio.Top := ActionsTop;
    WizardForm.NoRadio.Top := ActionsTop + ScaleY(32);
  end;
end;

function CanLaunchMadSense: Boolean;
var ExitCode: Integer;
begin
  Result := False;
#ifndef InstallerTestBuild
  if RegistrationFailed or DriversRestart or (ExpandConstant('{param:NOLAUNCH|0}') = '1') then Exit;
  if not LaunchChecked then begin
    OtherInstance := not Exec(ExpandConstant('{app}\MadSense.exe'), '--installer-check-running', '',
      SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0);
    LaunchChecked := True;
  end;
  Result := not OtherInstance;
#endif
end;

procedure InitializeWizard;
var ContentLeft, ContentWidth: Integer;
begin
  Log('Installer language: ' + ActiveLanguage);
  LoadLocalizedArtwork;
  HeaderMinHeight := WizardForm.MainPanel.Height;
  HeaderBodyGap := WizardForm.InnerNotebook.Top -
    (WizardForm.MainPanel.Top + WizardForm.MainPanel.Height);
  HeaderBodyBottom := WizardForm.InnerNotebook.Top + WizardForm.InnerNotebook.Height;
  HeaderSeparatorGap := WizardForm.Bevel1.Top -
    (WizardForm.MainPanel.Top + WizardForm.MainPanel.Height);
  ContentLeft := WizardForm.WizardBitmapImage.Width + ScaleX(30);
  ContentWidth := WizardForm.WelcomePage.ClientWidth - ContentLeft - ScaleX(30);
  WizardForm.WelcomeLabel1.Font.Name := 'Segoe UI';
  WizardForm.WelcomeLabel1.Font.Size := 22;
  WizardForm.WelcomeLabel1.Font.Style := [fsBold];
  WizardForm.WelcomeLabel1.SetBounds(ContentLeft, ScaleY(34), ContentWidth, ScaleY(83));
  WizardForm.WelcomeLabel2.Font.Size := 10;
  WizardForm.WelcomeLabel2.SetBounds(ContentLeft, ScaleY(136), ContentWidth, ScaleY(104));
  DesktopChoice := TNewCheckBox.Create(WizardForm);
  DesktopChoice.Parent := WizardForm.WelcomeLabel2.Parent;
  DesktopChoice.Left := WizardForm.WelcomeLabel2.Left;
  DesktopChoice.Top := ScaleY(267);
  DesktopChoice.Width := WizardForm.WelcomeLabel2.Width;
  DesktopChoice.Height := ScaleY(28);
  DesktopChoice.Font.Size := 10;
  DesktopChoice.Caption := CustomMessage('DesktopShortcut');
  DesktopChoice.Checked := WizardIsTaskSelected('desktopicon');
  WizardForm.PageNameLabel.Font.Style := [fsBold];
  WizardForm.PageNameLabel.Font.Size := 12;
  WizardForm.NextButton.Font.Style := [fsBold];
  WizardForm.FinishedHeadingLabel.SetBounds(ContentLeft, ScaleY(34), ContentWidth, ScaleY(64));
  WizardForm.FinishedHeadingLabel.Font.Size := 21;
  WizardForm.FinishedHeadingLabel.Font.Style := [fsBold];
  WizardForm.FinishedLabel.SetBounds(ContentLeft, ScaleY(116), ContentWidth, ScaleY(148));
  WizardForm.FinishedLabel.Font.Size := 10;
  WizardForm.RunList.SetBounds(ContentLeft, ScaleY(284), ContentWidth, ScaleY(52));
  WizardForm.RunList.Font.Size := 10;
  WizardForm.YesRadio.SetBounds(ContentLeft, ScaleY(284), ContentWidth, ScaleY(24));
  WizardForm.YesRadio.Font.Size := 10;
  WizardForm.NoRadio.SetBounds(ContentLeft, ScaleY(316), ContentWidth, ScaleY(24));
  WizardForm.NoRadio.Font.Size := 10;
  StageLabel := TNewStaticText.Create(WizardForm);
  StageLabel.Parent := WizardForm;
  StageLabel.Left := ScaleX(22);
  StageLabel.Top := WizardForm.CancelButton.Top + ScaleY(5);
  StageLabel.Caption := 'WINDOWS / {#AppVersion}';
  StageLabel.Font.Name := 'Consolas';
  StageLabel.Font.Size := 9;
#ifdef InstallerTestBuild
  WizardForm.Caption := CustomMessage('TestTitle');
  StageLabel.Caption := CustomMessage('TestStage');
#endif
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := PageID = wpSelectTasks;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpWelcome) and not WizardSilent then begin
    if DesktopChoice.Checked then WizardSelectTasks('desktopicon')
    else WizardSelectTasks('!desktopicon');
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := CustomMessage('ReadyMemo') + NewLine + NewLine + MemoTasksInfo;
#ifdef InstallerTestBuild
  Result := CustomMessage('TestMemo') + NewLine + NewLine + MemoDirInfo;
#endif
end;

function PrepareUninstall: Boolean;
var ExitCode: Integer; ResultFile, RunValue, MaintenanceResult: String;
begin
  Result := False;
  if FileExists(ExpandConstant('{app}\MadSense.exe')) then begin
    MaintenanceResult := ExpandConstant('{tmp}\uninstall-maintenance.txt');
    DeleteFile(MaintenanceResult);
    ExitCode := -1;
    if not Exec(ExpandConstant('{app}\MadSense.exe'), '--prepare-uninstall --install-path ' +
      Quote(ExpandConstant('{app}\MadSense.exe')) + ' --result-path ' + Quote(MaintenanceResult),
      '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then begin
      SuppressibleMsgBox(MaintenanceError(MaintenanceResult, ExitCode) + #13#10 +
        CustomMessage('RecoveryPreserved'), mbError, MB_OK, IDOK);
      Exit;
    end;
  end else if FileExists(ExpandConstant('{app}\hidden-controller.json')) then begin
    SuppressibleMsgBox(CustomMessage('MissingAppRecovery'),
      mbError, MB_OK, IDOK);
    Exit;
  end;
  ResultFile := ExpandConstant('{tmp}\unregister-result.ini');
  if FileExists(ExpandConstant('{app}\setup\DriverSetup.ps1')) then begin
    if not Helper('Unregister', ExpandConstant('{app}\setup\DriverSetup.ps1'), ResultFile, False, False) then begin
      SuppressibleMsgBox(FmtMessage(CustomMessage('UnregisterFailed'), [LastHelperError]), mbError, MB_OK, IDOK);
      Exit;
    end;
  end;
  // Delete startup only if it refers to this installation; preserve another copy.
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'MadSense', RunValue) then
    if CompareText(RunValue, Quote(ExpandConstant('{app}\MadSense.exe')) + ' --tray') = 0 then
      RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'MadSense');
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Only run after the user confirms; cancellation must not stop or unregister it.
  if CurUninstallStep = usUninstall then
    if not PrepareUninstall then Abort;
end;

procedure InitializeUninstallProgressForm;
var HeaderBottom, MinHeight, BodyGap, BodyBottom, SeparatorGap: Integer;
begin
  MinHeight := UninstallProgressForm.MainPanel.Height;
  HeaderBottom := UninstallProgressForm.MainPanel.Top + MinHeight;
  BodyGap := UninstallProgressForm.InnerNotebook.Top - HeaderBottom;
  BodyBottom := UninstallProgressForm.InnerNotebook.Top + UninstallProgressForm.InnerNotebook.Height;
  SeparatorGap := UninstallProgressForm.Bevel1.Top - HeaderBottom;
  UninstallProgressForm.PageNameLabel.Font.Style := [fsBold];
  UninstallProgressForm.PageNameLabel.Caption := CustomMessage('UninstallHeading');
  FitPageHeader(UninstallProgressForm.PageNameLabel, UninstallProgressForm.PageDescriptionLabel,
    UninstallProgressForm.MainPanel, UninstallProgressForm.Bevel1, UninstallProgressForm.InnerNotebook,
    MinHeight, BodyGap, BodyBottom, SeparatorGap);
end;
