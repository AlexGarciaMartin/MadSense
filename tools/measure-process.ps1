param(
    [string]$ProcessName = 'MadSense',
    [int]$Seconds = 15,
    [string]$Conditions = '',
    [string]$Out = ''
)
# Samples one process for $Seconds and reports CPU (one-core % and whole-machine %) plus memory.
# Same method as evidence/performance-v03.txt: process-only, excludes driver/kernel cost, not a latency measurement.
$ErrorActionPreference = 'Stop'
$p = Get-Process -Name $ProcessName | Select-Object -First 1
if (-not $p) { throw "Proceso '$ProcessName' no encontrado." }
$cpu0 = $p.TotalProcessorTime; $t0 = Get-Date
Start-Sleep -Seconds $Seconds
$p.Refresh()
$cpu = ($p.TotalProcessorTime - $cpu0).TotalSeconds
$elapsed = ((Get-Date) - $t0).TotalSeconds
$cores = [Environment]::ProcessorCount
$result = [ordered]@{
    Timestamp = (Get-Date).ToString('o')
    ProcessId = $p.Id
    ElapsedSeconds = [math]::Round($elapsed, 3)
    CpuSeconds = [math]::Round($cpu, 4)
    OneCorePercent = [math]::Round(100 * $cpu / $elapsed, 2)
    MachinePercent = [math]::Round(100 * $cpu / $elapsed / $cores, 3)
    LogicalProcessors = $cores
    WorkingSetMiB = [math]::Round($p.WorkingSet64 / 1MB, 2)
    PrivateMemoryMiB = [math]::Round($p.PrivateMemorySize64 / 1MB, 2)
    PriorityClass = $p.PriorityClass.ToString()
    Threads = $p.Threads.Count
    Conditions = $Conditions
}
$json = $result | ConvertTo-Json
if ($Out) { $json | Out-File -Encoding utf8 $Out }
$json
