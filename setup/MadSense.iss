; Compile through tools/build-installer.ps1. The installer is per-user; only
; the driver helper is elevated. Never use the legacy global --stop command.
#ifndef AppVersion
  #define AppVersion "0.4.0"
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
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19045
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableReadyPage=no
WizardStyle=modern light
WizardSizePercent=115
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
VersionInfoDescription=Instalador de MadSense
VersionInfoProductName={#ProductName}
VersionInfoCompany=Alex García Martín

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
WelcomeLabel1=INSTALA.%nMADSENSE.
WelcomeLabel2=DualSense por Bluetooth en PC.%n%nEste asistente instala MadSense y prepara los controladores que falten.%n%nGratis. Código abierto. Sin periodo de prueba.
WizardReady=TODO PREPARADO.
ReadyLabel1=Pulsa Instalar para preparar MadSense.
WizardInstalling=PREPARANDO MADSENSE.
InstallingLabel=Copiando los archivos de MadSense.
FinishedHeadingLabel=MADSENSE INSTALADO.
FinishedLabel=Empareja tu DualSense en Bluetooth de Windows si aún no lo has hecho. Abre MadSense y pulsa PS.%n%nPara las funciones nativas en Steam, desactiva Steam Input en las propiedades del juego.
FinishedRestartLabel=MadSense ya está instalado. Reinicia Windows para activar los controladores. Después abre MadSense y pulsa PS en el mando.%n%nEl mando debe estar emparejado por Bluetooth en Windows.
ButtonNext=&Continuar
ButtonInstall=&Instalar
ButtonFinish=&Terminar
ClickNext=Pulsa Continuar para empezar.
SelectTasksLabel2=Accesos directos:
WizardSelectTasks=A TU MANERA.
UninstallAppFullTitle=Desinstalar {#ProductName}
ConfirmUninstall=¿Quieres desinstalar %1?%n%nSe conservarán los ajustes y los controladores compartidos USBip e HidHide.

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; Flags: unchecked

[Files]
; Support comes first so it can be read without decompressing the entire app.
#ifdef InstallerTestBuild
Source: "..\tests\installer\FakeDriverSetup.ps1"; DestDir: "{tmp}\support"; DestName: "DriverSetup.ps1"; Flags: dontcopy
#else
Source: "DriverSetup.ps1"; DestDir: "{tmp}\support"; Flags: dontcopy
#endif
Source: "DriverSetup.Core.psm1"; DestDir: "{tmp}\support"; Flags: dontcopy
Source: "dependencies.json"; DestDir: "{tmp}\support"; Flags: dontcopy
Source: "{#DependencyDirectory}\*.exe"; DestDir: "{tmp}\drivers"; Flags: dontcopy
; The temporary copy provides maintenance commands even when upgrading 0.4.
; Inno reuses duplicate source data in the archive.
Source: "{#PublishDirectory}\*"; DestDir: "{tmp}\payload"; Flags: dontcopy recursesubdirs
Source: "{#PublishDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifndef InstallerTestBuild
Source: "DriverSetup.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
#endif
Source: "DriverSetup.Core.psm1"; DestDir: "{app}\setup"; Flags: ignoreversion
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
Filename: "{app}\MadSense.exe"; Description: "Abrir MadSense"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser; Check: CanLaunchMadSense

[Code]
var
  DriversRestart, HelpersExtracted, PayloadExtracted, DriverPrepared: Boolean;
  RegistrationPending, RegistrationFailed, OtherInstance, LaunchChecked: Boolean;
  LastHelperError, RegistrationError: String;
  StageLabel: TNewStaticText;
  DesktopChoice: TNewCheckBox;

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
    ' -AppPath ' + Quote(ExpandConstant('{app}\MadSense.exe'));
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
    Started := Exec(PowerShellPath, Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
  Result := Started and ((ExitCode = 0) or (ExitCode = 3010)) and
    (GetIniString('Result', 'Success', '0', ResultFile) = '1');
  // A partial driver installation may still require a restart.
  if (ExitCode = 3010) or (GetIniString('Result', 'RebootRequired', '0', ResultFile) = '1') then DriversRestart := True;
  if not Result then begin
    ErrorText := GetIniString('Result', 'Error', '', ResultFile);
    if ErrorText = '' then ErrorText := 'No se ha podido completar el paso. Código: ' + IntToStr(ExitCode) + '.';
    LastHelperError := ErrorText;
    Log('Driver helper failed: ' + ErrorText);
  end else begin
    RegistrationPending := GetIniString('Result', 'RegistrationPending', '0', ResultFile) = '1';
  end;
end;

function MaintenanceError(const ResultFile: String; ExitCode: Integer): String;
var Lines: TArrayOfString; I: Integer;
begin
  Result := '';
  if LoadStringsFromFile(ResultFile, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do begin
      if Result <> '' then Result := Result + #13#10;
      Result := Result + Lines[I];
    end;
  if Result = '' then Result := 'No se ha podido preparar MadSense. Código: ' + IntToStr(ExitCode) + '.';
  if ExitCode = 3 then Result := Result + #13#10#13#10 +
    'Reinicia Windows y vuelve a intentarlo. Si HidHide sigue sin responder, repáralo desde Aplicaciones instaladas de Windows. Conserva hidden-controller.json.';
  Log('Maintenance failed (' + IntToStr(ExitCode) + '): ' + Result);
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
  WizardForm.StatusLabel.Caption := 'Comprobando los controladores...';
  if not Helper('Inspect', ExpandConstant('{tmp}\support\DriverSetup.ps1'), ResultFile, False, False) then begin
    Result := LastHelperError;
    Exit;
  end;
  if GetIniString('Result', 'NeedsInstall', '1', ResultFile) = '1' then begin
    WizardForm.StatusLabel.Caption := 'Preparando USBip e HidHide. Acepta el permiso de Windows.';
    ExtractTemporaryFiles('{tmp}\drivers\*');
    if not Helper('Install', ExpandConstant('{tmp}\support\DriverSetup.ps1'), ResultFile, True, False) then begin
      Result := LastHelperError;
      NeedsRestart := DriversRestart;
      if DriversRestart then Result := Result + ' Reinicia Windows y vuelve a ejecutar el instalador.';
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
begin
  if (CurPageID = wpFinished) and RegistrationFailed then begin
    WizardForm.FinishedHeadingLabel.Caption := 'FALTA UN PASO.';
    WizardForm.FinishedLabel.Caption := 'MadSense se ha copiado, pero HidHide no ha autorizado la aplicación.' + #13#10#13#10 +
      RegistrationError + #13#10#13#10 + 'Vuelve a ejecutar este instalador para reparar la instalación.';
    if DriversRestart then WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10 + 'Antes, reinicia Windows.';
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
begin
  WizardForm.WelcomeLabel1.Font.Name := 'Segoe UI';
  WizardForm.WelcomeLabel1.Font.Size := 24;
  WizardForm.WelcomeLabel1.Font.Style := [fsBold];
  WizardForm.WelcomeLabel1.Height := ScaleY(94);
  WizardForm.WelcomeLabel2.Top := WizardForm.WelcomeLabel1.Top + WizardForm.WelcomeLabel1.Height + ScaleY(12);
  WizardForm.WelcomeLabel2.Height := ScaleY(145);
  DesktopChoice := TNewCheckBox.Create(WizardForm);
  DesktopChoice.Parent := WizardForm.WelcomeLabel2.Parent;
  DesktopChoice.Left := WizardForm.WelcomeLabel2.Left;
  DesktopChoice.Top := WizardForm.WelcomeLabel2.Top + WizardForm.WelcomeLabel2.Height + ScaleY(10);
  DesktopChoice.Width := WizardForm.WelcomeLabel2.Width;
  DesktopChoice.Caption := 'Crear acceso directo en el escritorio';
  DesktopChoice.Checked := WizardIsTaskSelected('desktopicon');
  WizardForm.PageNameLabel.Font.Style := [fsBold];
  WizardForm.PageNameLabel.Font.Size := 12;
  WizardForm.NextButton.Font.Style := [fsBold];
  WizardForm.FinishedHeadingLabel.Font.Size := 17;
  WizardForm.FinishedHeadingLabel.Font.Style := [fsBold];
  StageLabel := TNewStaticText.Create(WizardForm);
  StageLabel.Parent := WizardForm;
  StageLabel.Left := ScaleX(12);
  StageLabel.Top := WizardForm.CancelButton.Top + ScaleY(5);
  StageLabel.Caption := 'MADSENSE / WINDOWS';
  StageLabel.Font.Name := 'Consolas';
  StageLabel.Font.Size := 9;
#ifdef InstallerTestBuild
  WizardForm.Caption := 'MadSense — PRUEBA DEL INSTALADOR';
  StageLabel.Caption := 'PRUEBA / SIN CAMBIOS EN CONTROLADORES';
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
  Result := 'MadSense para tu usuario de Windows.' + NewLine + NewLine +
    'Se comprobarán USBip e HidHide. Si falta alguno, Windows pedirá permiso para instalarlo.' + NewLine +
    'Los controladores están incluidos en el instalador.' + NewLine + NewLine +
    'Tus ajustes se conservan al actualizar.' + NewLine + NewLine + MemoTasksInfo;
#ifdef InstallerTestBuild
  Result := 'PRUEBA AISLADA DEL INSTALADOR.' + NewLine +
    'Los pasos de controladores se simulan. La aplicación no se abre al terminar.' + NewLine + NewLine + MemoDirInfo;
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
        'No se borrará el registro de recuperación del mando.', mbError, MB_OK, IDOK);
      Exit;
    end;
  end else if FileExists(ExpandConstant('{app}\hidden-controller.json')) then begin
    SuppressibleMsgBox('Falta MadSense.exe y hay una recuperación del mando pendiente. Reinstala MadSense en esta carpeta antes de desinstalar.',
      mbError, MB_OK, IDOK);
    Exit;
  end;
  ResultFile := ExpandConstant('{tmp}\unregister-result.ini');
  if FileExists(ExpandConstant('{app}\setup\DriverSetup.ps1')) then begin
    if not Helper('Unregister', ExpandConstant('{app}\setup\DriverSetup.ps1'), ResultFile, False, False) then begin
      SuppressibleMsgBox('HidHide no ha confirmado la retirada de MadSense: ' + LastHelperError + #13#10 +
        'No se han eliminado los archivos. Vuelve a intentarlo.', mbError, MB_OK, IDOK);
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
begin
  UninstallProgressForm.PageNameLabel.Font.Style := [fsBold];
  UninstallProgressForm.PageNameLabel.Caption := 'HASTA LA PRÓXIMA.';
end;
