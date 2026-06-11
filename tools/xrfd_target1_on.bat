@echo off
rem Double-click: enable Target 1 (saved to EEPROM, survives reboot).
title XRFD - target 1 ON
call "%~dp0xrfd_send.bat" "target 1 on"
pause
