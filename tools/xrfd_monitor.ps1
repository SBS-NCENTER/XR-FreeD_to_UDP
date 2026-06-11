# XRFD diagnostics monitor (Windows PowerShell, no install required)
#
# Listens for the Arduino's 5-second status broadcast on UDP 50999 and prints
# each line with a timestamp and the sender address (= the Arduino's IP).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\xrfd_monitor.ps1
#
# One-time firewall rule (run once in an ADMIN PowerShell), otherwise Windows
# drops inbound UDP broadcasts:
#   New-NetFirewallRule -DisplayName "XRFD diag" -Direction Inbound -Protocol UDP -LocalPort 50999 -Action Allow
#
# Example output:
#   [14:02:31.120] [10.10.204.100] XRFD up=123 ip=10.10.204.100 rx=7380 dhcp=0/0 rtr=Y t0=A,7380,0,0 t1=off t2=off t3=off

param([int]$Port = 50999)

$udp = New-Object System.Net.Sockets.UdpClient
$udp.ExclusiveAddressUse = $false
$udp.Client.SetSocketOption(
  [System.Net.Sockets.SocketOptionLevel]::Socket,
  [System.Net.Sockets.SocketOptionName]::ReuseAddress, $true)
$udp.Client.Bind((New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, $Port)))

$ep = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
Write-Host "Listening for XRFD diagnostics on UDP $Port ... (Ctrl+C to stop)"
try {
  while ($true) {
    $bytes = $udp.Receive([ref]$ep)
    $line = [System.Text.Encoding]::ASCII.GetString($bytes)
    $ts = Get-Date -Format "HH:mm:ss.fff"
    Write-Host "[$ts] [$($ep.Address)] $line"
  }
}
finally { $udp.Close() }
