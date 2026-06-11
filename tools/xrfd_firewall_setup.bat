@echo off
rem One-time setup - RIGHT-CLICK this file and choose "Run as administrator".
rem   1) inbound UDP 50999 : receive the device's diag broadcast
rem   2) inbound TCP 10000  : let OTHER PCs/phones open the dashboard hosted here
title XRFD firewall setup
powershell -NoProfile -Command "New-NetFirewallRule -DisplayName 'XRFD diag' -Direction Inbound -Protocol UDP -LocalPort 50999 -Action Allow; New-NetFirewallRule -DisplayName 'XRFD dashboard' -Direction Inbound -Protocol TCP -LocalPort 10000 -Action Allow"
echo Done. If you saw an access error, re-run as administrator.
pause
