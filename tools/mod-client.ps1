# Sends DSX-protocol packets to the mod receiver, the way a game mod does, and prints the replies.
# Use it to check that MadSense (or whatever listens on the port) applies trigger, light and LED
# instructions. Without arguments it runs a short demo: right trigger resistance, left trigger
# weapon click, orange lightbar, player 2 LEDs, then a reset.
#   tools\mod-client.ps1                      demo sequence
#   tools\mod-client.ps1 -Status              status query only
#   tools\mod-client.ps1 -Json '{"instructions":[{"type":1,"parameters":[0,2,21,3,8]}]}'
param([switch]$Status, [string]$Json, [int]$Port = 6969, [int]$HoldMs = 2500)
$ErrorActionPreference = 'Stop'
$client = New-Object System.Net.Sockets.UdpClient
$client.Client.ReceiveTimeout = 1500
$target = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Loopback), $Port
function Send($packet) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($packet)
    [void]$client.Send($bytes, $bytes.Length, $target)
    try {
        $from = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Any), 0
        $reply = [Text.Encoding]::UTF8.GetString($client.Receive([ref]$from))
        "<- $reply"
    } catch { "<- (no reply: $($_.Exception.Message))" }
}
if ($Json) { "-> $Json"; Send $Json; return }
if ($Status) { Send '{"instructions":[{"type":0,"parameters":[0]}]}'; return }
$steps = @(
    @{ Text = 'R2: resistencia desde el 30 %, fuerza 6 (modo v2 Resistance=13)'; Packet = '{"instructions":[{"type":1,"parameters":[0,2,13,3,6]}]}' },
    @{ Text = 'L2: clic de arma 2-6, fuerza 8 (modo v3 WEAPON=22)'; Packet = '{"instructions":[{"type":1,"parameters":[0,1,22,2,6,8]}]}' },
    @{ Text = 'Lightbar naranja + LEDs jugador 2'; Packet = '{"instructions":[{"type":2,"parameters":[0,249,84,40]},{"type":6,"parameters":[0,1]}]}' },
    @{ Text = 'R2: vibración 20 Hz (v2 AutomaticGun=17)'; Packet = '{"instructions":[{"type":1,"parameters":[0,2,17,2,8,20]}]}' },
    @{ Text = 'Reset: el juego recupera el control'; Packet = '{"instructions":[{"type":7,"parameters":[0]}]}' }
)
foreach ($step in $steps) {
    ""; "== $($step.Text)"; "-> $($step.Packet)"
    Send $step.Packet
    Start-Sleep -Milliseconds $HoldMs
}
$client.Dispose()
