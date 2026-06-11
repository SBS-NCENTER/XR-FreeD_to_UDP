# XRFD remote control (Windows PowerShell, no install required)
#
# Sends a command to the Arduino's UDP control port (50998) and prints the
# reply. If -Ip is omitted, the device is AUTO-DISCOVERED by listening for one
# diagnostics broadcast on UDP 50999 (the packet's source address is the
# Arduino's IP) - max wait ~12s (broadcasts are sent every 5s).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\xrfd_ctl.ps1 "target 1 off"
#   powershell -ExecutionPolicy Bypass -File .\xrfd_ctl.ps1 "target 1 on"
#   powershell -ExecutionPolicy Bypass -File .\xrfd_ctl.ps1 "status" -Ip 10.10.204.100
#
# Commands: "target <0-3> on|off" (applied immediately, saved to EEPROM),
#           "status" (returns the diagnostics line)
#
# Note: auto-discovery needs the same inbound firewall rule as the monitor
# (UDP 50999). Sending a command and receiving its reply needs NO rule
# (reply matches the outbound flow).

param(
  [Parameter(Mandatory = $true)][string]$Command,
  [string]$Ip = "",
  [int]$CtrlPort = 50998,
  [int]$DiagPort = 50999
)

if ($Ip -eq "") {
  Write-Host "Discovering device via diag broadcast on UDP $DiagPort (max 12s)..."
  $disc = New-Object System.Net.Sockets.UdpClient
  $disc.ExclusiveAddressUse = $false
  $disc.Client.SetSocketOption(
    [System.Net.Sockets.SocketOptionLevel]::Socket,
    [System.Net.Sockets.SocketOptionName]::ReuseAddress, $true)
  $disc.Client.Bind((New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, $DiagPort)))
  $disc.Client.ReceiveTimeout = 12000
  $ep = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
  try {
    [void]$disc.Receive([ref]$ep)
    $Ip = $ep.Address.ToString()
    Write-Host "Found device: $Ip"
  }
  catch {
    Write-Error ("No diag broadcast received in 12s. Pass -Ip <address> " +
      "explicitly, and check the inbound firewall rule for UDP $DiagPort.")
    exit 1
  }
  finally { $disc.Close() }
}

$udp = New-Object System.Net.Sockets.UdpClient
$udp.Client.ReceiveTimeout = 3000
$bytes = [System.Text.Encoding]::ASCII.GetBytes($Command)
[void]$udp.Send($bytes, $bytes.Length, $Ip, $CtrlPort)
$ep = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
try {
  $reply = $udp.Receive([ref]$ep)
  Write-Host ([System.Text.Encoding]::ASCII.GetString($reply))
}
catch {
  Write-Error "No reply from ${Ip}:$CtrlPort within 3s."
  exit 1
}
finally { $udp.Close() }
