param([switch]$Hardware)
$ErrorActionPreference = 'Stop'
$sdk = Get-Command dotnet -ErrorAction SilentlyContinue
$sdkPath = if ($sdk) { $sdk.Source } else { Join-Path $PSScriptRoot '..\..\work\dotnet\dotnet.exe' }
if (!(Test-Path -LiteralPath $sdkPath)) { throw 'Instala .NET SDK 10 para ejecutar los tests.' }
$filter = 'FullyQualifiedName~BluetoothAudioReportBuilderTests|FullyQualifiedName~DualSenseBtAudioPipelineTests|FullyQualifiedName~DualSenseAudioLaneTests|FullyQualifiedName~UsbIpServerTests|FullyQualifiedName~IsoSchedulerTests|FullyQualifiedName~EmulationServiceUsbIpTests|FullyQualifiedName~VirtualDualSenseController|FullyQualifiedName~VirtualXbox360Controller|FullyQualifiedName~DualSenseFeature|FullyQualifiedName~NativeHapticsQueueTests|FullyQualifiedName~DualSenseAudioForwarderTests'
$oldIntegration = $env:EMULATION_INTEGRATION
$oldExe = $env:MADSENSE_EXE
if ($Hardware) {
    if (Get-Process DSX,MadSense -ErrorAction SilentlyContinue) { throw 'Cierra DSX y MadSense antes de las pruebas físicas.' }
    $env:EMULATION_INTEGRATION = '1'
    $env:MADSENSE_EXE = Join-Path $PSScriptRoot 'app\MadSense.exe'
    $filter += '|FullyQualifiedName~DualSenseAudioEndToEndTests|FullyQualifiedName~Xbox360EndToEndTests|FullyQualifiedName~XInputRoundTripTests|FullyQualifiedName~MadSenseHardwareTests'
}
try {
    & $sdkPath test (Join-Path $PSScriptRoot 'tests\DualSenseClient.Tests\DualSenseClient.Tests.csproj') -c Release --filter $filter --logger 'trx;LogFileName=validation.trx' --results-directory (Join-Path $PSScriptRoot 'test-results')
    if ($LASTEXITCODE -ne 0) { throw 'Hay pruebas fallidas. Revisa test-results.' }
} finally {
    $env:EMULATION_INTEGRATION = $oldIntegration
    $env:MADSENSE_EXE = $oldExe
}




