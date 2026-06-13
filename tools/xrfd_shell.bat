@echo off
rem XRFD interactive shell - double-click to run.
rem Auto-discovers the device; type 'help' inside for commands.
title XRFD Shell
rem Prefer PowerShell 7 (pwsh): Windows PowerShell 5.1 can be silently blocked
rem by a per-program inbound firewall rule, stopping UDP 50999 device discovery.
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0xrfd_shell.ps1" %*
pause
