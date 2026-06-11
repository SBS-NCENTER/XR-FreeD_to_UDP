@echo off
rem Double-click: disable Target 1 (saved to EEPROM, survives reboot).
title XRFD - target 1 OFF
call "%~dp0xrfd_send.bat" "target 1 off"
pause
