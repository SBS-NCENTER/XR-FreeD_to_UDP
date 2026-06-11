@echo off
rem XRFD Dashboard - double-click to start the web GUI bridge on this PC.
rem Then open http://localhost:10000 (this PC) or http://<this-pc-ip>:10000
rem from any browser on the LAN (PC / iPad / phone).
rem One-time firewall rules: run xrfd_firewall_setup.bat as administrator.
title XRFD Dashboard (web bridge :10000)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0xrfd_dashboard.ps1" %*
pause
