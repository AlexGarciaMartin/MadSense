param(
    [string]$RepositoryRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-LanguageContract([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "Installer language check: $Message" }
}

function Read-Utf8([string]$Path) {
    Assert-LanguageContract (Test-Path -LiteralPath $Path -PathType Leaf) "Missing file: $Path"
    # Reject malformed UTF-8 instead of silently publishing replacement characters.
    $encoding = New-Object System.Text.UTF8Encoding($false, $true)
    return [System.IO.File]::ReadAllText($Path, $encoding)
}

function Read-Catalog([string[]]$Paths) {
    $catalog = @{ Messages = @{}; CustomMessages = @{} }
    foreach ($path in $Paths) {
        $section = ''
        $lineNumber = 0
        foreach ($line in ((Read-Utf8 $path) -split '\r?\n')) {
            $lineNumber++
            if ($line -match '^\s*(;.*)?$') { continue }
            if ($line -match '^\s*\[([^\]]+)\]\s*$') {
                $section = $Matches[1]
                Assert-LanguageContract ($catalog.ContainsKey($section)) "Unexpected section [$section] in ${path}:$lineNumber"
                continue
            }
            Assert-LanguageContract ($catalog.ContainsKey($section)) "Message outside a section in ${path}:$lineNumber"
            Assert-LanguageContract ($line -match '^\s*([A-Za-z][A-Za-z0-9_]*)=(.*)$') "Invalid message in ${path}:$lineNumber"
            $key = $Matches[1]
            $value = $Matches[2]
            Assert-LanguageContract (-not $catalog[$section].ContainsKey($key)) "Duplicate [$section] key '$key' in ${path}:$lineNumber"
            $catalog[$section][$key] = $value
        }
    }
    return $catalog
}

function Get-MessageTokens([string]$Value, [string]$Context) {
    # Count arguments and deliberate line breaks, not words or their order. %% is a
    # literal percent sign and must not be mistaken for an argument or a newline.
    $tokens = @{}
    for ($i = 0; $i -lt $Value.Length; $i++) {
        if ($Value[$i] -ne '%') { continue }
        Assert-LanguageContract ($i + 1 -lt $Value.Length) "Incomplete percent token in $Context"
        $i++
        $token = [string]$Value[$i]
        Assert-LanguageContract ($token -cmatch '^[1-9n%]$') "Unknown percent token '%$token' in $Context"
        if ($token -eq '%') { continue }
        if (-not $tokens.ContainsKey($token)) { $tokens[$token] = 0 }
        $tokens[$token]++
    }
    return (($tokens.Keys | Sort-Object | ForEach-Object { "${_}:$($tokens[$_])" }) -join ',')
}

function Assert-Sidebar([string]$Path, [string]$Language) {
    Assert-LanguageContract (Test-Path -LiteralPath $Path -PathType Leaf) "Missing 3x sidebar for '$Language': $Path"
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    Assert-LanguageContract ($bytes.Length -ge 54) "Truncated BMP for '$Language'"
    Assert-LanguageContract ($bytes[0] -eq 0x42 -and $bytes[1] -eq 0x4D) "Sidebar for '$Language' is not a BMP"
    $declaredSize = [BitConverter]::ToUInt32($bytes, 2)
    $pixelOffset = [BitConverter]::ToUInt32($bytes, 10)
    $headerSize = [BitConverter]::ToUInt32($bytes, 14)
    $width = [BitConverter]::ToInt32($bytes, 18)
    $height = [BitConverter]::ToInt32($bytes, 22)
    $planes = [BitConverter]::ToUInt16($bytes, 26)
    Assert-LanguageContract ($declaredSize -eq $bytes.Length -and $headerSize -ge 40 -and $pixelOffset -ge (14 + $headerSize) -and $pixelOffset -lt $bytes.Length -and $planes -eq 1) "Invalid BMP header for '$Language'"
    Assert-LanguageContract ($width -eq 492 -and [Math]::Abs([long]$height) -eq 942) "Sidebar for '$Language' must be 492x942 (3x), found ${width}x${height}"
}

$setupDirectory = Join-Path $RepositoryRoot 'setup'
$installerPath = Join-Path $setupDirectory 'MadSense.iss'
$installer = Read-Utf8 $installerPath
$languageSection = [regex]::Match($installer, '(?ms)^\[Languages\]\s*\r?\n(?<body>.*?)(?=^\[|\z)')
Assert-LanguageContract $languageSection.Success 'MadSense.iss has no [Languages] section'
$languages = @()
$languageIds = @{}
foreach ($line in ($languageSection.Groups['body'].Value -split '\r?\n')) {
    if ($line -match '^\s*(;.*)?$') { continue }
    $name = [regex]::Match($line, '(?i)(?:^|;)\s*Name:\s*"(?<id>[a-z][a-z0-9_]*)"')
    $files = [regex]::Match($line, '(?i)(?:^|;)\s*MessagesFile:\s*"(?<files>[^"]+)"')
    Assert-LanguageContract ($name.Success -and $files.Success) "Cannot read language entry: $line"
    $id = $name.Groups['id'].Value
    Assert-LanguageContract (-not $languageIds.ContainsKey($id)) "Duplicate language '$id'"
    $languageIds[$id] = $true
    $messageFiles = @($files.Groups['files'].Value.Split(',') | ForEach-Object { $_.Trim() })
    $localFiles = @($messageFiles | Where-Object { $_ -notmatch '^compiler:' } | ForEach-Object { Join-Path $setupDirectory $_ })
    Assert-LanguageContract ($localFiles.Count -gt 0) "Language '$id' has no local MadSense catalog"
    $languages += [pscustomobject]@{ Id = $id; Files = $messageFiles; Catalog = (Read-Catalog $localFiles) }
}
Assert-LanguageContract ($languages.Count -gt 1) 'The installer must offer more than one language'
Assert-LanguageContract ($languages[0].Id -ceq 'english') 'English must be the first language, as the fallback'
Assert-LanguageContract ($languages[0].Files[0] -ieq 'compiler:Default.isl') 'English must inherit compiler:Default.isl'

# A global override would replace every translation, even if the local catalogs
# are complete. Keep these overrides in the per-language .isl files instead.
foreach ($sectionName in @('Messages', 'CustomMessages')) {
    foreach ($sectionMatch in [regex]::Matches($installer, "(?ms)^\[$sectionName\]\s*\r?\n(?<body>.*?)(?=^\[|\z)")) {
        foreach ($line in ($sectionMatch.Groups['body'].Value -split '\r?\n')) {
            Assert-LanguageContract ($line -match '^\s*(;.*)?$') "MadSense.iss contains a global [$sectionName] override: $line"
        }
    }
}

$referencedKeys = @{}
foreach ($match in [regex]::Matches($installer, "(?i)CustomMessage\s*\(\s*'(?<key>[A-Za-z][A-Za-z0-9_]*)'\s*\)|\{cm:(?<key>[A-Za-z][A-Za-z0-9_]*)(?:[,}])")) {
    $referencedKeys[$match.Groups['key'].Value] = $true
}
Assert-LanguageContract ($referencedKeys.Count -gt 0) 'No custom message references found in MadSense.iss'

$baseline = $languages[0].Catalog
foreach ($language in $languages) {
    foreach ($sectionName in @('Messages', 'CustomMessages')) {
        $keys = @($language.Catalog[$sectionName].Keys | Sort-Object)
        $expectedKeys = @($baseline[$sectionName].Keys | Sort-Object)
        Assert-LanguageContract ($keys.Count -gt 0 -and ($keys -join ',') -ceq ($expectedKeys -join ',')) "[$sectionName] key mismatch in '$($language.Id)'"
        foreach ($key in $expectedKeys) {
            $expectedTokens = Get-MessageTokens $baseline[$sectionName][$key] "english/$sectionName/$key"
            $actualTokens = Get-MessageTokens $language.Catalog[$sectionName][$key] "$($language.Id)/$sectionName/$key"
            Assert-LanguageContract ($expectedTokens -ceq $actualTokens) "Argument or line-break mismatch in '$($language.Id)/$sectionName/$key': expected '$expectedTokens', found '$actualTokens'"
        }
    }
    foreach ($key in $referencedKeys.Keys) {
        Assert-LanguageContract ($language.Catalog.CustomMessages.ContainsKey($key) -and -not [string]::IsNullOrWhiteSpace($language.Catalog.CustomMessages[$key])) "Missing or empty referenced custom message '$key' in '$($language.Id)'"
    }
    $sidebar = Join-Path $setupDirectory "assets/localized/sidebar-$($language.Id).bmp"
    Assert-Sidebar $sidebar $language.Id
    Write-Host "PASS $($language.Id): catalog keys, arguments, line breaks and 492x942 BMP"
}
Write-Host "Installer language checks passed: $($languages.Count) languages, $($referencedKeys.Count) referenced custom messages."
