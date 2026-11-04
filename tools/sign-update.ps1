param(
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$Version,
    [ValidateSet('stable','preview')][string]$Channel = 'preview'
)
$ErrorActionPreference = 'Stop'
# The private key is non-exportable, stored by Windows CNG for this Windows account.
# Never place it in the repository, installer, test bundle or CI logs.
$key = [Security.Cryptography.RSACng]::new([Security.Cryptography.CngKey]::Open('MadSense.UpdateSigning.v1'))
try {
    $file = Get-Item -LiteralPath $Installer
    if ($file.Name -ne "MadSense-$Version-Setup.exe") { throw 'Installer filename/version mismatch.' }
    $tag = if ($Channel -eq 'preview') { "update-preview-$Version" } else { "v$Version" }
    $payload = [ordered]@{
        Version=$Version; Channel=$Channel; Filename=$file.Name; Bytes=$file.Length
        Sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        DownloadUrl="https://github.com/AlexGarciaMartin/MadSense/releases/download/$tag/$($file.Name)"
    } | ConvertTo-Json -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
    $signature = $key.SignData($bytes,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pss)
    $envelope = @{payload=[Convert]::ToBase64String($bytes);signature=[Convert]::ToBase64String($signature)} | ConvertTo-Json
    [IO.File]::WriteAllText(($file.FullName + '.update.json'),$envelope,[Text.UTF8Encoding]::new($false))
    Write-Host "Signed descriptor: $($file.FullName).update.json"
} finally { $key.Dispose() }
