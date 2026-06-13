@echo off
rem XRFD diagnostics monitor - double-click to run, close window to stop.
rem Shows the Arduino's 5-second status broadcast (UDP 50999), incl. its IP.
rem Requires the one-time firewall rule: run xrfd_firewall_setup.bat as admin.
title XRFD Diagnostics Monitor (UDP 50999)
rem Prefer PowerShell 7 (pwsh): Windows PowerShell 5.1 can be silently blocked
rem by a per-program inbound firewall rule, stopping the UDP 50999 broadcast.
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"
%PS% -NoProfile -ExecutionPolicy Bypass -Command "$u=New-Object Net.Sockets.UdpClient; $u.ExclusiveAddressUse=$false; $u.Client.SetSocketOption([Net.Sockets.SocketOptionLevel]::Socket,[Net.Sockets.SocketOptionName]::ReuseAddress,$true); $u.Client.Bind((New-Object Net.IPEndPoint([Net.IPAddress]::Any,50999))); $e=New-Object Net.IPEndPoint(0,0); Write-Host 'Listening for XRFD diagnostics on UDP 50999 ... (close window to stop)'; while($true){ $b=$u.Receive([ref]$e); Write-Host ('[{0}] {1}' -f (Get-Date -Format HH:mm:ss), [Text.Encoding]::ASCII.GetString($b)) }"
pause
