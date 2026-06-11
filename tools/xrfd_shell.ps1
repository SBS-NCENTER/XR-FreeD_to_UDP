# XRFD interactive shell (Windows PowerShell, no install required)
#
# Launch via xrfd_shell.bat (double-click) or:
#   powershell -ExecutionPolicy Bypass -File .\xrfd_shell.ps1 [-Ip 10.10.204.100]
#
# Type 'help' inside the shell for commands.

param(
  [string]$Ip = "",
  [int]$CtrlPort = 50998,
  [int]$DiagPort = 50999
)

function New-DiagListener {
  $u = New-Object Net.Sockets.UdpClient
  $u.ExclusiveAddressUse = $false
  $u.Client.SetSocketOption(
    [Net.Sockets.SocketOptionLevel]::Socket,
    [Net.Sockets.SocketOptionName]::ReuseAddress, $true)
  $u.Client.Bind((New-Object Net.IPEndPoint([Net.IPAddress]::Any, $DiagPort)))
  return $u
}

function Discover-Device {
  Write-Host "Discovering device via diag broadcast on UDP $DiagPort (max 12s)..."
  $d = New-DiagListener
  $d.Client.ReceiveTimeout = 12000
  $ep = New-Object Net.IPEndPoint(0, 0)
  try {
    [void]$d.Receive([ref]$ep)
    Write-Host "Found device: $($ep.Address)" -ForegroundColor Green
    return $ep.Address.ToString()
  }
  catch {
    Write-Host "No diag broadcast received (check firewall rule for UDP $DiagPort)." -ForegroundColor Yellow
    return ""
  }
  finally { $d.Close() }
}

function Send-Cmd([string]$cmd) {
  if ($script:Ip -eq "") {
    Write-Host "No device IP - run 'discover' first or restart with -Ip <addr>." -ForegroundColor Yellow
    return $null
  }
  $u = New-Object Net.Sockets.UdpClient
  $u.Client.ReceiveTimeout = 3000
  $b = [Text.Encoding]::ASCII.GetBytes($cmd)
  [void]$u.Send($b, $b.Length, $script:Ip, $CtrlPort)
  $ep = New-Object Net.IPEndPoint(0, 0)
  try { return [Text.Encoding]::ASCII.GetString($u.Receive([ref]$ep)) }
  catch { return "(no reply from $($script:Ip):$CtrlPort within 3s)" }
  finally { $u.Close() }
}

function Stream-Diagnosis {
  Write-Host "Streaming diagnostics (every 5s)... press ESC / Q / Ctrl+C to stop." -ForegroundColor Cyan
  $u = New-DiagListener
  $u.Client.ReceiveTimeout = 200
  $ep = New-Object Net.IPEndPoint(0, 0)
  $prevCtrlC = [Console]::TreatControlCAsInput
  [Console]::TreatControlCAsInput = $true
  try {
    while ($true) {
      if ([Console]::KeyAvailable) {
        $k = [Console]::ReadKey($true)
        $isCtrlC = ($k.Key -eq 'C') -and (($k.Modifiers -band [ConsoleModifiers]::Control) -ne 0)
        if ($k.Key -eq 'Escape' -or $k.Key -eq 'Q' -or $isCtrlC) { break }
      }
      try {
        $b = $u.Receive([ref]$ep)
        Write-Host ("[{0}] {1}" -f (Get-Date -Format HH:mm:ss), [Text.Encoding]::ASCII.GetString($b))
      }
      catch [Net.Sockets.SocketException] { } # receive timeout - poll keys again
    }
  }
  finally {
    [Console]::TreatControlCAsInput = $prevCtrlC
    $u.Close()
    Write-Host "(stopped)"
  }
}

function Show-Help {
  Write-Host @"

  XRFD shell commands
  -------------------
  help | -h | -help | ?      show this help
  discover                   find the device IP via diag broadcast
  diagnosis                  stream diagnostics lines (stop: ESC / Q / Ctrl+C)
  status                     one-shot device status line
  targets                    list targets 0-3 (enabled / ip / port)
  ChangeTarget -n <0-3> -i <a.b.c.d> -p <port>
                             set target's ip+port AND enable it (EEPROM saved)
  on <0-3>                   enable a target            (EEPROM saved)
  off <0-3>                  disable a target           (EEPROM saved)
  reboot                     reboot the device (asks for confirmation)
  raw <text>                 send raw command text to the control port
  exit | quit                leave the shell

  Diagnostics line: XRFD up=<s> ms=<millis> ip=<addr> rx=<frames>
                    dhcp=<ok/fail> rtr=<Y/N>
                    t<i>=<A|B>,<sentOk>,<sendFail>,<skipped>   (off = disabled)

"@
}

# ── startup ──────────────────────────────────────────────────────────────────
if ($Ip -eq "") { $Ip = Discover-Device }
Write-Host ""
Write-Host "XRFD interactive shell - device: $(if ($Ip) { $Ip } else { '(unknown - run discover)' })"
Write-Host "Type 'help' for commands."
Write-Host ""

while ($true) {
  $line = Read-Host "XRFD"
  if ([string]::IsNullOrWhiteSpace($line)) { continue }
  $tokens = -split $line
  $cmd = $tokens[0].ToLower()

  switch -Regex ($cmd) {
    '^(help|-h|-help|\?)$' { Show-Help; break }
    '^(exit|quit)$' { return }
    '^discover$' { $Ip = Discover-Device; break }
    '^diagnosis$' { Stream-Diagnosis; break }
    '^status$' { Send-Cmd "status" | Write-Host; break }
    '^targets$' { Send-Cmd "targets" | Write-Host; break }
    '^changetarget$' {
      $n = $null; $i = $null; $p = $null
      for ($k = 1; $k -lt $tokens.Count - 1; $k += 2) {
        switch ($tokens[$k].ToLower()) {
          '-n' { $n = $tokens[$k + 1] }
          '-i' { $i = $tokens[$k + 1] }
          '-p' { $p = $tokens[$k + 1] }
        }
      }
      if ($null -eq $n -or $null -eq $i -or $null -eq $p) {
        Write-Host "Usage: ChangeTarget -n <0-3> -i <a.b.c.d> -p <port>" -ForegroundColor Yellow
      }
      else { Send-Cmd "target $n set $i $p" | Write-Host }
      break
    }
    '^on$' {
      if ($tokens.Count -ge 2) { Send-Cmd "target $($tokens[1]) on" | Write-Host }
      else { Write-Host "Usage: on <0-3>" -ForegroundColor Yellow }
      break
    }
    '^off$' {
      if ($tokens.Count -ge 2) { Send-Cmd "target $($tokens[1]) off" | Write-Host }
      else { Write-Host "Usage: off <0-3>" -ForegroundColor Yellow }
      break
    }
    '^reboot$' {
      $ans = Read-Host "Reboot the device? (y/N)"
      if ($ans -match '^[yY]') { Send-Cmd "reboot" | Write-Host }
      else { Write-Host "(cancelled)" }
      break
    }
    '^raw$' {
      if ($line.Length -gt 4) { Send-Cmd $line.Substring(4) | Write-Host }
      else { Write-Host "Usage: raw <text>" -ForegroundColor Yellow }
      break
    }
    default { Write-Host "Unknown command '$($tokens[0])' - type 'help'." -ForegroundColor Yellow }
  }
}
