@echo off
rem Shared helper - sends one control command to the Arduino (UDP 50998) and
rem prints the reply. Auto-discovers the device IP from the diag broadcast
rem (UDP 50999, max 12s) unless XRFD_IP is set by the caller.
rem Usage: xrfd_send.bat "target 1 off"
rem Prefer PowerShell 7 (pwsh): Windows PowerShell 5.1 can be silently blocked
rem by a per-program inbound firewall rule, stopping UDP 50999 device discovery.
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"
%PS% -NoProfile -ExecutionPolicy Bypass -Command "$cmd='%~1'; $ip='%XRFD_IP%'; if($ip -eq ''){ Write-Host 'Discovering device via diag broadcast (max 12s)...'; $d=New-Object Net.Sockets.UdpClient; $d.ExclusiveAddressUse=$false; $d.Client.SetSocketOption([Net.Sockets.SocketOptionLevel]::Socket,[Net.Sockets.SocketOptionName]::ReuseAddress,$true); $d.Client.Bind((New-Object Net.IPEndPoint([Net.IPAddress]::Any,50999))); $d.Client.ReceiveTimeout=12000; $e=New-Object Net.IPEndPoint(0,0); try{ [void]$d.Receive([ref]$e); $ip=$e.Address.ToString(); Write-Host ('Found device: '+$ip) } catch { Write-Host 'ERROR: no diag broadcast received (check firewall rule / LAN).'; exit 1 } finally { $d.Close() } }; Write-Host ('Sending to '+$ip+':50998  ->  '+$cmd); $u=New-Object Net.Sockets.UdpClient; $u.Client.ReceiveTimeout=3000; $b=[Text.Encoding]::ASCII.GetBytes($cmd); [void]$u.Send($b,$b.Length,$ip,50998); $e2=New-Object Net.IPEndPoint(0,0); try{ Write-Host ('Reply: '+[Text.Encoding]::ASCII.GetString($u.Receive([ref]$e2))) } catch { Write-Host ('ERROR: no reply from '+$ip+':50998 within 3s.') } finally { $u.Close() }"
