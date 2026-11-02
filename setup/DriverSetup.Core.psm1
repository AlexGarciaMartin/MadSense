Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Get-MadDependencyManifest {
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'dependencies.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schemaVersion -ne 1 -or $manifest.drivers.Count -ne 2) { throw 'Manifiesto de controladores no valido.' }
    return $manifest
}

function Get-MadPlatform {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $cpu = Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1
    [pscustomobject]@{ Build = [int]$os.BuildNumber; Architecture = [int]$cpu.Architecture; Is64BitProcess = [Environment]::Is64BitProcess }
}

function Assert-MadPlatform($Manifest) {
    $platform = Get-MadPlatform
    # Win32_Processor.Architecture 9 is native x64. ARM64's x64 emulation cannot load x64 drivers.
    if ($platform.Architecture -ne 9) { throw 'Este instalador necesita Windows x64 (Intel o AMD). Windows ARM64 no es compatible con estos controladores.' }
    if (-not $platform.Is64BitProcess) { throw 'Ejecuta el instalador con PowerShell de 64 bits.' }
    if ($platform.Build -lt [int]$Manifest.minimumWindowsBuild) { throw 'MadSense necesita Windows 10 22H2 (19045) o Windows 11, de 64 bits.' }
}

function Test-MadAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-MadProgramFiles {
    $directory = $env:ProgramW6432
    if (-not $directory) { $directory = [Environment]::GetFolderPath('ProgramFiles') }
    return $directory
}

function Get-MadExecutableVersion([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $info = (Get-Item -LiteralPath $Path).VersionInfo
    try { return [version]('{0}.{1}.{2}.{3}' -f $info.FileMajorPart,$info.FileMinorPart,$info.FileBuildPart,$info.FilePrivatePart) }
    catch { return $null }
}

function Get-MadUninstallEntries {
    return @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue)
}

function Get-MadDriverExecutable($Spec) {
    # Respect the location of an existing vendor installation instead of assuming C:.
    $namePattern = if ($Spec.id -eq 'USBip') { '^USBip(?:\s|$)' } else { '^HidHide$' }
    $relative = if ($Spec.id -eq 'USBip') { 'usbip.exe' } else { 'x64\HidHideCLI.exe' }
    foreach ($entry in (Get-MadUninstallEntries)) {
        if ($entry.PSObject.Properties['DisplayName'] -and $entry.PSObject.Properties['InstallLocation'] -and
            $entry.DisplayName -match $namePattern -and $entry.InstallLocation) {
            $candidate = [IO.Path]::Combine($entry.InstallLocation,$relative)
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    return Join-Path (Get-MadProgramFiles) $Spec.relativeExecutable
}

function ConvertTo-MadNativeArgument([string]$Value) {
    if ($Value -and $Value -notmatch '[\s"]') { return $Value }
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-MadNative([string]$FilePath, [string[]]$Arguments) {
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $FilePath
    $start.Arguments = ($Arguments | ForEach-Object { ConvertTo-MadNativeArgument $_ }) -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "No se pudo ejecutar $FilePath." }
        # Read both pipes concurrently: a verbose installer cannot block the other pipe.
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $stdout.Result; Error = $stderr.Result }
    } finally { $process.Dispose() }
}

function Get-MadDriverState($Spec) {
    $exe = Get-MadDriverExecutable $Spec
    $version = Get-MadExecutableVersion $exe
    $service = Get-CimInstance Win32_SystemDriver -Filter ("Name='{0}'" -f $Spec.service) -ErrorAction Stop
    $servicePresent = $null -ne $service
    $operational = $servicePresent -and $service.State -eq 'Running'
    if ($Spec.id -eq 'USBip') {
        $devices = @(Get-CimInstance Win32_PnPEntity -Filter "Service='usbip2_ude'" -ErrorAction Stop)
        $operational = $operational -and @($devices | Where-Object { $_.ConfigManagerErrorCode -eq 0 }).Count -gt 0
    } elseif ($operational -and $version) {
        $probe = Invoke-MadNative $exe @('--app-list')
        $operational = $probe.ExitCode -eq 0
    }
    $versionOk = $null -ne $version -and $version -ge [version]$Spec.version
    [pscustomobject]@{
        Id = $Spec.id; Executable = $exe; Version = $version
        FilesPresent = $null -ne $version; VersionOk = $versionOk
        ServicePresent = $servicePresent; Operational = [bool]$operational
        Healthy = [bool]($versionOk -and $operational)
    }
}

function Get-MadDriverStates($Manifest) {
    return @($Manifest.drivers | ForEach-Object { Get-MadDriverState $_ })
}

function Get-MadDriverSummary($States) {
    return ($States | ForEach-Object {
        $state = if ($_.Healthy) { 'listo' } elseif (-not $_.FilesPresent) { 'falta' } elseif (-not $_.VersionOk) { 'version antigua' } else { 'no operativo' }
        '{0}: {1}' -f $_.Id,$state
    }) -join '; '
}

function Test-MadRebootPending {
    return (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or
        (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or
        ($null -ne (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue))
}

function New-MadProtectedStagingDirectory {
    # ProgramData is shared; create a fresh unguessable directory then remove inherited
    # write rights before copying/running any elevated installer from it.
    $directory = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) ('MadSense-Setup-' + [guid]::NewGuid().ToString('N'))
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true,$false)
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl',$inherit,'None','Allow')
        $acl.AddAccessRule($rule)
    }
    [IO.Directory]::CreateDirectory($directory,$acl) | Out-Null
    return $directory
}

function Remove-MadProtectedStagingDirectory([string]$Path) {
    $root = [IO.Path]::GetFullPath([Environment]::GetFolderPath('CommonApplicationData')).TrimEnd('\')
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($full) -ine $root -or [IO.Path]::GetFileName($full) -notmatch '^MadSense-Setup-[0-9a-f]{32}$') {
        throw 'Ruta temporal del instalador no valida; no se ha borrado.'
    }
    if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Recurse -Force }
}

function Get-MadVerifiedPayload($Spec, [string]$PayloadDirectory, [string]$StagingDirectory) {
    $destination = Join-Path $StagingDirectory $Spec.file
    $bundled = if ($PayloadDirectory) { Join-Path $PayloadDirectory $Spec.file } else { $null }
    if ($bundled -and (Test-Path -LiteralPath $bundled -PathType Leaf)) {
        Copy-Item -LiteralPath $bundled -Destination $destination -Force
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $Spec.url -OutFile $destination -UseBasicParsing -ErrorAction Stop
    }
    if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ine $Spec.sha256) { throw "La descarga de $($Spec.id) no coincide con su SHA-256 oficial. No se ha ejecutado." }
    $signature = Get-AuthenticodeSignature -LiteralPath $destination
    if ($signature.Status -ne [Management.Automation.SignatureStatus]::Valid) { throw "La firma digital de $($Spec.id) no se puede validar. No se ha ejecutado." }
    return $destination
}

function Install-MadDrivers($Manifest, [string]$PayloadDirectory) {
    if (-not (Test-MadAdministrator)) { throw 'Se necesitan permisos de administrador para instalar los controladores.' }
    $changed = $false
    $staging = $null
    try {
        foreach ($spec in $Manifest.drivers) {
            $before = Get-MadDriverState $spec
            if ($before.Healthy) { continue }
            if ($before.Version -and $before.Version -gt [version]$spec.version) {
                throw "$($spec.id) tiene una version mas reciente pero no esta operativo. Reinicia Windows o repara esa version desde Aplicaciones instaladas. MadSense no la sustituira por una anterior."
            }
            if (-not $staging) { $staging = New-MadProtectedStagingDirectory }
            $installer = Get-MadVerifiedPayload $spec $PayloadDirectory $staging
            $run = Invoke-MadNative $installer @($spec.arguments)
            if ($run.ExitCode -notin @(0,3010,1641)) { throw "El instalador de $($spec.id) ha fallado (codigo $($run.ExitCode)). $($run.Error)" }
            # Keep this information even if verification or the second dependency fails.
            $script:MadDriverChangesRequireReboot = $true
            $after = Get-MadDriverState $spec
            if (-not $after.VersionOk -or -not $after.ServicePresent) { throw "$($spec.id) no ha dejado instalados los archivos y el servicio esperados. Revisa el registro del instalador o repara el controlador." }
            # Both official packages require reboot. Do not infer it only from the exit
            # code or unrelated Windows Update registry keys (USBip also returns 0).
            $changed = $true
        }
        return $changed
    } finally {
        if ($staging) { Remove-MadProtectedStagingDirectory $staging }
    }
}

function Assert-MadApplicationPath([string]$AppPath, [bool]$MustExist) {
    if (-not $AppPath -or -not [IO.Path]::IsPathRooted($AppPath) -or $AppPath -match '[\r\n"]') { throw 'La ruta de MadSense debe ser absoluta.' }
    $full = [IO.Path]::GetFullPath($AppPath)
    if ([IO.Path]::GetFileName($full) -ine 'MadSense.exe') { throw 'La autorizacion debe apuntar a MadSense.exe.' }
    if ($MustExist -and -not (Test-Path -LiteralPath $full -PathType Leaf)) { throw 'MadSense.exe no esta instalado en la ruta indicada.' }
    return $full
}

function Test-MadApplicationListed([string]$Output, [string]$AppPath) {
    foreach ($line in ($Output -split '\r?\n')) {
        if ($line -match '^\s*--app-reg\s+"([^"]+)"\s*$' -and $Matches[1].Equals($AppPath,[StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Set-MadRegistration($Manifest, [string]$Action, [string]$AppPath, [bool]$AllowPendingReboot) {
    $full = Assert-MadApplicationPath $AppPath ($Action -eq 'Register')
    $spec = $Manifest.drivers | Where-Object { $_.id -eq 'HidHide' }
    $state = Get-MadDriverState $spec
    if (-not $state.FilesPresent) {
        if ($Action -eq 'Unregister') { return $false }
        throw 'HidHide no esta instalado; no se ha autorizado MadSense.'
    }
    if (-not $state.Operational) {
        if ($AllowPendingReboot -and $Action -eq 'Register' -and $state.VersionOk -and $state.ServicePresent) { return $true }
        throw 'HidHide no responde. Reinicia Windows antes de continuar; no se ha cambiado su lista de aplicaciones.'
    }
    $before = Invoke-MadNative $state.Executable @('--app-list')
    if ($before.ExitCode -ne 0) { throw "No se pudo consultar HidHide (codigo $($before.ExitCode))." }
    $expected = $Action -eq 'Register'
    if ((Test-MadApplicationListed $before.Output $full) -eq $expected) { return $false }
    $command = if ($expected) { '--app-reg' } else { '--app-unreg' }
    $change = Invoke-MadNative $state.Executable @($command,$full)
    if ($change.ExitCode -ne 0) { throw "HidHide no pudo $($Action.ToLowerInvariant()) MadSense (codigo $($change.ExitCode)). $($change.Error)" }
    $after = Invoke-MadNative $state.Executable @('--app-list')
    if ($after.ExitCode -ne 0 -or (Test-MadApplicationListed $after.Output $full) -ne $expected) { throw 'HidHide no confirma el cambio de autorizacion. No se da la operacion por terminada.' }
    return $false
}

function Invoke-MadDriverSetup {
    [CmdletBinding()]
    param([ValidateSet('Inspect','Install','Register','Unregister')][string]$Action, [string]$AppPath, [string]$PayloadDirectory, [switch]$AllowPendingReboot)
    $result = [ordered]@{ Success = $false; RebootRequired = $false; NeedsInstall = $false; RegistrationPending = $false; Error = ''; DriverSummary = '' }
    $script:MadDriverChangesRequireReboot = $false
    try {
        $manifest = Get-MadDependencyManifest
        Assert-MadPlatform $manifest
        switch ($Action) {
            'Inspect' {
                $states = Get-MadDriverStates $manifest
                $result.NeedsInstall = @($states | Where-Object { -not $_.Healthy }).Count -gt 0
                $result.DriverSummary = Get-MadDriverSummary $states
                # A system-wide reboot request is surfaced, without making any changes.
                $result.RebootRequired = Test-MadRebootPending
            }
            'Install' {
                $changed = Install-MadDrivers $manifest $PayloadDirectory
                $result.RebootRequired = $changed -or (Test-MadRebootPending)
                $states = Get-MadDriverStates $manifest
                $result.DriverSummary = Get-MadDriverSummary $states
                $result.NeedsInstall = @($states | Where-Object { -not $_.VersionOk -or -not $_.ServicePresent }).Count -gt 0
            }
            default {
                $result.RegistrationPending = Set-MadRegistration $manifest $Action $AppPath $AllowPendingReboot.IsPresent
                $result.RebootRequired = $result.RegistrationPending
            }
        }
        $result.Success = $true
    } catch {
        $result.Error = $_.Exception.Message
        $result.RebootRequired = $result.RebootRequired -or $script:MadDriverChangesRequireReboot
    }
    return [pscustomobject]$result
}

function Write-MadDriverResult($Result, [string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path) -or [IO.Path]::GetExtension($Path) -ine '.ini') { throw 'La ruta del resultado debe ser absoluta y terminar en .ini.' }
    $full = [IO.Path]::GetFullPath($Path)
    $lines = @('[Result]')
    foreach ($name in @('Success','RebootRequired','NeedsInstall','RegistrationPending')) { $lines += $name + '=' + [int][bool]$Result.$name }
    foreach ($name in @('Error','DriverSummary')) { $lines += $name + '=' + (($Result.$name -replace '[\r\n\x00]', ' ') -replace '^\s+', '') }
    # UTF-16 LE is read by Windows GetPrivateProfileString/Inno Setup, including Spanish.
    [IO.File]::WriteAllLines($full,$lines,[Text.Encoding]::Unicode)
}

Export-ModuleMember -Function Invoke-MadDriverSetup,Write-MadDriverResult,Get-MadDependencyManifest
