# Safe driver-helper regression tests. No real driver installer, native HidHide command,
# registry write, app launch, or process termination is used by these tests.
$ErrorActionPreference = 'Stop'
$modulePath = Join-Path $PSScriptRoot '..\..\setup\DriverSetup.Core.psm1'
$script:passed = 0
function Run-Case([string]$Name,[scriptblock]$Body) {
    Remove-Module DriverSetup.Core -ErrorAction SilentlyContinue
    $module = Import-Module $modulePath -Force -PassThru
    & $module {
        function script:Get-MadPlatform { [pscustomobject]@{ Build=19045; Architecture=9; Is64BitProcess=$true } }
        function script:Test-MadAdministrator { $true }
        function script:Test-MadRebootPending { $false }
        function script:Get-MadUninstallEntries { @() }
        function script:Invoke-MadNative { throw 'An unmocked native process would have been launched.' }
        function script:Get-MadVerifiedPayload { throw 'An unmocked download would have been started.' }
        function script:New-MadProtectedStagingDirectory { 'C:\audit-staging' }
        function script:Remove-MadProtectedStagingDirectory { }
        function script:Assert-MadApplicationPath([string]$AppPath,[bool]$MustExist) { $AppPath }
    }
    & $module $Body
    $script:passed++
    Write-Host "PASS $Name"
}

Run-Case 'Native ARM64 is rejected even with 64-bit emulation' {
    function script:Get-MadPlatform { [pscustomobject]@{ Build=26100; Architecture=12; Is64BitProcess=$true } }
    $r = Invoke-MadDriverSetup Inspect
    if ($r.Success -or $r.Error -notmatch 'ARM64') { throw 'ARM64 was accepted.' }
}
Run-Case 'Old Windows build rejected before checking drivers' {
    function script:Get-MadPlatform { [pscustomobject]@{ Build=18362; Architecture=9; Is64BitProcess=$true } }
    $r = Invoke-MadDriverSetup Inspect
    if ($r.Success -or $r.Error -notmatch '19045') { throw 'Unsupported Windows accepted.' }
}
Run-Case '32-bit helper rejected' {
    function script:Get-MadPlatform { [pscustomobject]@{ Build=26100; Architecture=9; Is64BitProcess=$false } }
    if ((Invoke-MadDriverSetup Inspect).Success) { throw '32-bit helper was accepted.' }
}
Run-Case 'An EXE alone does not count as an installed, operational driver' {
    function script:Get-MadExecutableVersion { [version]'9.9.9.9' }
    function script:Get-CimInstance { @() }
    $r = Invoke-MadDriverSetup Inspect
    if (-not $r.Success -or -not $r.NeedsInstall -or $r.DriverSummary -notmatch 'no operativo') { throw 'Executable-only detection regressed.' }
}
Run-Case 'Existing vendor installation on a custom drive is respected' {
    function script:Get-MadUninstallEntries { [pscustomobject]@{ DisplayName='HidHide'; InstallLocation='D:\Drivers\HidHide' } }
    function script:Test-Path { $true }
    $manifest = Get-MadDependencyManifest
    $found = Get-MadDriverExecutable $manifest.drivers[1]
    if ($found -ne 'D:\Drivers\HidHide\x64\HidHideCLI.exe') { throw 'Vendor install location ignored.' }
}
Run-Case 'A running USBip service without a healthy device is not ready' {
    function script:Get-MadExecutableVersion { [version]'0.9.8.1' }
    function script:Get-CimInstance([string]$ClassName,[string]$Filter) {
        if ($ClassName -eq 'Win32_SystemDriver') { return [pscustomobject]@{ State='Running' } }
        return [pscustomobject]@{ ConfigManagerErrorCode=10 }
    }
    $manifest = Get-MadDependencyManifest
    $state = Get-MadDriverState $manifest.drivers[0]
    if ($state.Healthy) { throw 'A PnP error was ignored.' }
}
Run-Case 'USBip-only installation requires reboot even with exit code zero' {
    $script:installed = $false
    $script:installerArguments = @()
    function script:Get-MadDriverState($spec) {
        $ok = $spec.id -eq 'HidHide' -or $script:installed
        [pscustomobject]@{ Id=$spec.id; Version=if($ok){[version]$spec.version}else{$null}; FilesPresent=$ok; VersionOk=$ok; ServicePresent=$ok; Operational=$ok; Healthy=$ok }
    }
    function script:Get-MadVerifiedPayload { 'C:\audit-staging\USBip.exe' }
    function script:Invoke-MadNative([string]$FilePath,[string[]]$Arguments) {
        $script:installerArguments=$Arguments
        $script:installed=$true
        [pscustomobject]@{ ExitCode=0;Output='';Error='' }
    }
    $r = Invoke-MadDriverSetup Install
    if (-not $r.Success -or -not $r.RebootRequired -or $r.NeedsInstall) { throw 'USBip reboot was lost.' }
    if ($script:installerArguments -notcontains '/TYPE=compact' -or $script:installerArguments -notcontains '/RESTARTEXITCODE=3010') { throw 'USBip compact/reboot arguments missing.' }
}
Run-Case 'Newer unhealthy driver is never silently downgraded' {
    function script:Get-MadDriverState($spec) { [pscustomobject]@{ Version=[version]'99.0'; Healthy=$false } }
    $r = Invoke-MadDriverSetup Install
    if ($r.Success -or $r.Error -notmatch 'reciente') { throw 'Newer driver would be downgraded.' }
}
Run-Case 'Failed native driver installer is not treated as success' {
    function script:Get-MadDriverState { [pscustomobject]@{ Version=$null; Healthy=$false } }
    function script:Get-MadVerifiedPayload { 'C:\audit-staging\USBip.exe' }
    function script:Invoke-MadNative { [pscustomobject]@{ ExitCode=1; Output=''; Error='installer failed' } }
    $r = Invoke-MadDriverSetup Install
    if ($r.Success -or $r.Error -notmatch 'c[oó]digo 1') { throw 'Installer failure ignored.' }
}
Run-Case 'Partial installation failure preserves the required restart' {
    $script:installedUsb=$false
    function script:Get-MadDriverState($spec) {
        $ok = $spec.id -eq 'USBip' -and $script:installedUsb
        [pscustomobject]@{ Version=if($ok){[version]$spec.version}else{$null}; Healthy=$ok; VersionOk=$ok; ServicePresent=$ok }
    }
    function script:Get-MadVerifiedPayload($spec) { return 'C:\audit-staging\' + $spec.file }
    function script:Invoke-MadNative([string]$FilePath,[string[]]$Arguments) {
        if ($FilePath -match 'USBip') {
            $script:installedUsb=$true
            return [pscustomobject]@{ ExitCode=3010; Output=''; Error='' }
        }
        return [pscustomobject]@{ ExitCode=1; Output=''; Error='HidHide failed' }
    }
    $r = Invoke-MadDriverSetup Install
    if ($r.Success -or -not $r.RebootRequired -or $r.Error -notmatch 'HidHide') { throw 'Restart requirement was lost after partial install.' }
}
Run-Case 'HidHide authorization exit code 7 is an error' {
    function script:Get-MadDriverState { [pscustomobject]@{ FilesPresent=$true; VersionOk=$true; ServicePresent=$true; Operational=$true; Executable='C:\audit\HidHideCLI.exe' } }
    function script:Invoke-MadNative([string]$FilePath,[string[]]$Arguments) {
        if ($Arguments[0] -eq '--app-list') { return [pscustomobject]@{ ExitCode=0; Output=''; Error='' } }
        return [pscustomobject]@{ ExitCode=7; Output=''; Error='denied' }
    }
    $r = Invoke-MadDriverSetup Register -AppPath 'C:\Users\Player\MadSense.exe'
    if ($r.Success -or $r.Error -notmatch 'c[oó]digo 7') { throw 'HidHide failure was ignored.' }
}
Run-Case 'HidHide must confirm the app is present after a successful command' {
    function script:Get-MadDriverState { [pscustomobject]@{ FilesPresent=$true; VersionOk=$true; ServicePresent=$true; Operational=$true; Executable='C:\audit\HidHideCLI.exe' } }
    function script:Invoke-MadNative { [pscustomobject]@{ ExitCode=0; Output=''; Error='' } }
    $r = Invoke-MadDriverSetup Register -AppPath 'C:\Users\Player\MadSense.exe'
    if ($r.Success -or $r.Error -notmatch 'confirma') { throw 'Unverified authorization succeeded.' }
}
Run-Case 'Register and unregister affect only the exact supplied path' {
    $script:listed=$false
    $script:seen=@()
    function script:Get-MadDriverState { [pscustomobject]@{ FilesPresent=$true; VersionOk=$true; ServicePresent=$true; Operational=$true; Executable='C:\audit\HidHideCLI.exe' } }
    function script:Invoke-MadNative([string]$FilePath,[string[]]$Arguments) {
        $script:seen += $Arguments
        if ($Arguments[0] -eq '--app-reg') { $script:listed=$true }
        if ($Arguments[0] -eq '--app-unreg') { $script:listed=$false }
        $output='--app-reg "C:\another copy\MadSense.exe"'
        if ($script:listed) { $output += "`r`n" + '--app-reg "C:\Users\Player\MadSense.exe"' }
        [pscustomobject]@{ ExitCode=0; Output=$output; Error='' }
    }
    $r = Invoke-MadDriverSetup Register -AppPath 'C:\Users\Player\MadSense.exe'
    if (-not $r.Success -or $r.RegistrationPending) { throw 'Registration failed.' }
    $r = Invoke-MadDriverSetup Unregister -AppPath 'C:\Users\Player\MadSense.exe'
    if (-not $r.Success -or $script:listed -or $script:seen -contains 'C:\another copy\MadSense.exe') { throw 'Unregistration affected another installation.' }
}
Run-Case 'Pending HidHide is deferred only with explicit reboot context' {
    function script:Get-MadDriverState { [pscustomobject]@{ FilesPresent=$true; VersionOk=$true; ServicePresent=$true; Operational=$false; Executable='C:\audit\HidHideCLI.exe' } }
    $r = Invoke-MadDriverSetup Register -AppPath 'C:\Users\Player\MadSense.exe'
    if ($r.Success) { throw 'Unexplained HidHide failure was hidden.' }
    $r = Invoke-MadDriverSetup Register -AppPath 'C:\Users\Player\MadSense.exe' -AllowPendingReboot
    if (-not $r.Success -or -not $r.RegistrationPending -or -not $r.RebootRequired) { throw 'Pending reboot contract failed.' }
}
Run-Case 'A missing HidHide can be unregistered without reinstalling it' {
    function script:Get-MadDriverState { [pscustomobject]@{ FilesPresent=$false } }
    $r = Invoke-MadDriverSetup Unregister -AppPath 'C:\Users\Player\MadSense.exe'
    if (-not $r.Success) { throw 'Uninstall required a missing shared dependency.' }
}
Run-Case 'INI result preserves Unicode and cannot inject a second section' {
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('madsense-driver-result-' + [guid]::NewGuid().ToString('N') + '.ini')
    try {
        $r = [pscustomobject]@{ Success=$false; RebootRequired=$true; NeedsInstall=$true; RegistrationPending=$false; Error="Acceso denegado`n[Injected]"; DriverSummary='USBip: falta' }
        Write-MadDriverResult $r $temporary
        $bytes = [IO.File]::ReadAllBytes($temporary)
        $text = [IO.File]::ReadAllText($temporary,[Text.Encoding]::Unicode)
        if ($bytes[0] -ne 255 -or $bytes[1] -ne 254 -or $text -notmatch 'Error=Acceso denegado \[Injected\]' -or $text -notmatch 'RebootRequired=1') { throw 'Bad INI serialization.' }
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}
Run-Case 'All six helper languages have complete keys and matching format placeholders' {
    $languages=@('english','spanish','french','german','italian','brazilianportuguese')
    $expected=@($script:MadDriverMessages.english.Keys | Sort-Object)
    if (@($script:MadDriverMessages.Keys).Count -ne $languages.Count) { throw 'Unexpected language set.' }
    foreach ($language in $languages) {
        $actual=@($script:MadDriverMessages[$language].Keys | Sort-Object)
        if (Compare-Object $expected $actual) { throw "Incomplete helper translation: $language" }
        foreach ($key in $expected) {
            $source=$script:MadDriverMessages.english[$key]
            $translation=$script:MadDriverMessages[$language][$key]
            if ([string]::IsNullOrWhiteSpace($translation)) { throw "Empty translation: $language/$key" }
            $sourceSlots=@([regex]::Matches($source,'\{\d+\}') | ForEach-Object Value | Sort-Object)
            $translatedSlots=@([regex]::Matches($translation,'\{\d+\}') | ForEach-Object Value | Sort-Object)
            if (($sourceSlots -join ',') -cne ($translatedSlots -join ',')) { throw "Format placeholders differ: $language/$key" }
            $formatted=Get-MadDriverMessage $key @('driver-name',123,'native-detail') -Language $language
            if ($formatted -match '\{\d+\}') { throw "Unexpanded placeholder: $language/$key" }
        }
    }
}
Run-Case 'Unsupported and missing translations fall back to English' {
    $expected='Administrator permission is required to install the drivers.'
    if ((Get-MadDriverMessage AdminRequired -Language 'unsupported') -cne $expected) { throw 'Unknown language did not fall back to English.' }
    $script:MadDriverMessages.french.Remove('AdminRequired')
    if ((Get-MadDriverMessage AdminRequired -Language 'french') -cne $expected) { throw 'Missing message did not fall back to English.' }
    function script:Test-MadAdministrator { $false }
    $r=Invoke-MadDriverSetup Install -Language 'unsupported'
    if ($r.Success -or $r.Error -cne $expected) { throw 'Helper did not pass the language through to its error.' }
}
Run-Case 'Selected language reaches native failure details without losing diagnostics' {
    function script:Get-MadDriverState { [pscustomobject]@{ Version=$null; Healthy=$false } }
    function script:Get-MadVerifiedPayload { 'C:\audit-staging\USBip.exe' }
    function script:Invoke-MadNative { [pscustomobject]@{ ExitCode=27; Output=''; Error='native detail {kept} é' } }
    foreach ($language in @('english','spanish','french','german','italian','brazilianportuguese')) {
        $r=Invoke-MadDriverSetup Install -Language $language
        $expected=Get-MadDriverMessage DriverInstallFailed @('USBip',27,'native detail {kept} é') -Language $language
        if ($r.Success -or $r.Error -cne $expected -or $r.Error -notmatch 'native detail \{kept\} é') { throw "Lost language or native detail: $language" }
    }
}
Run-Case 'Every localized failure survives the UTF-16 INI result protocol' {
    function script:Test-MadAdministrator { $false }
    $temporary=Join-Path ([IO.Path]::GetTempPath()) ('madsense-localized-result-'+[guid]::NewGuid().ToString('N')+'.ini')
    try {
        foreach ($language in @('english','spanish','french','german','italian','brazilianportuguese')) {
            $r=Invoke-MadDriverSetup Install -Language $language
            Write-MadDriverResult $r $temporary
            $text=[IO.File]::ReadAllText($temporary,[Text.Encoding]::Unicode)
            $expected=Get-MadDriverMessage AdminRequired -Language $language
            if ($text -notmatch ('(?m)^Error='+[regex]::Escape($expected)+'\r?$')) { throw "Corrupt localized INI: $language" }
        }
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}
Remove-Module DriverSetup.Core -ErrorAction SilentlyContinue
# Start-Process or Inno between pwsh and Windows PowerShell preserves the parent's
# PSModulePath. Direct native invocation of powershell.exe from pwsh sanitizes it
# and would miss this regression. Run this suite under pwsh as well as PS 5.1.
$fakePath=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'FakeDriverSetup.ps1'))
foreach ($bits in 32,64) {
    $system=if ($bits -eq 32) { 'SysWOW64' } elseif ([Environment]::Is64BitProcess) { 'System32' } else { 'Sysnative' }
    $temporary=Join-Path ([IO.Path]::GetTempPath()) ('madsense-inherited-helper-'+[guid]::NewGuid().ToString('N')+'.ini')
    $process=$null
    try {
        $info=New-Object Diagnostics.ProcessStartInfo
        $info.FileName=Join-Path $env:WINDIR ($system+'\WindowsPowerShell\v1.0\powershell.exe')
        # Resolve production cmdlets in module scope without querying a real driver
        # or invoking a download/signature operation. Then exercise the fake entrypoint.
        $probe='$ErrorActionPreference=''Stop''; $ProgressPreference=''SilentlyContinue''; Import-Module '''+$modulePath.Replace("'","''")+''' -Force; & (Get-Module DriverSetup.Core) { foreach ($name in ''Import-PowerShellDataFile'',''Get-FileHash'',''Get-AuthenticodeSignature'',''Get-CimInstance'',''Get-Content'',''Set-Content'') { $command=Get-Command $name -ErrorAction Stop; if ($command.Module.Version.Major -ge 7) { throw (''Incompatible module inherited: ''+$name) } } }; & '''+$fakePath.Replace("'","''")+''' -Action Inspect -Language english -ResultPath '''+$temporary.Replace("'","''")+''''
        $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($probe))
        $info.Arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand '+$encoded
        $info.UseShellExecute=$false
        $info.CreateNoWindow=$true
        $info.RedirectStandardOutput=$true
        $info.RedirectStandardError=$true
        $process=[Diagnostics.Process]::Start($info)
        $stdout=$process.StandardOutput.ReadToEndAsync()
        $stderr=$process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) { throw "Inherited-environment helper did not exit (PID $($process.Id))." }
        $expectedExit=if ($bits -eq 64) { 0 } else { 1 }
        if ($process.ExitCode -ne $expectedExit -or -not (Test-Path -LiteralPath $temporary)) {
            throw "Inherited-environment helper failed ($bits bits): $($stderr.Result) $($stdout.Result)"
        }
        $result=[IO.File]::ReadAllText($temporary,[Text.Encoding]::Unicode)
        $expectedSuccess=if ($bits -eq 64) { 1 } else { 0 }
        if ($result -notmatch "(?m)^Success=$expectedSuccess`r?$" -or $result -notmatch "(?m)^ProcessBits=$bits`r?$") { throw "Wrong fake result under inherited environment ($bits bits)." }
        if ($bits -eq 32 -and $result -notmatch '(?m)^Error=Run the installer with 64-bit PowerShell\.') { throw 'Wrong 32-bit failure language.' }
        if ($stderr.Result) { throw "Unexpected fake helper error: $($stderr.Result)" }
        $script:passed++
        Write-Host "PASS Fake $bits-bit helper with inherited PSModulePath from PowerShell $($PSVersionTable.PSVersion)"
    } finally {
        if ($process) { $process.Dispose() }
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}
Write-Host "Driver helper: $script:passed safe regression tests passed."
