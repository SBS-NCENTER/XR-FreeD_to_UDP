@echo off
rem XRFD Dashboard - double-click to start the web GUI bridge on this PC.
rem Then open http://localhost:10000 (this PC) or http://<this-pc-ip>:10000
rem from any browser on the LAN (PC / iPad / phone).
rem One-time firewall rules: run xrfd_firewall_setup.bat as administrator.
rem
rem Prefer PowerShell 7 (pwsh) when available: Windows PowerShell 5.1
rem (powershell.exe) can be silently blocked by a per-program inbound
rem firewall rule, which stops the UDP 50999 diag broadcast from arriving
rem and leaves the dashboard with no device data. Falls back to 5.1 if
rem pwsh is not installed.
title XRFD Dashboard (web bridge :10000)
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0xrfd_dashboard.ps1" %*
pause
