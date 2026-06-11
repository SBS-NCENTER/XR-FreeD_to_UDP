@echo off
rem Double-click: interactive control - type any command.
rem Commands: "target <0-3> on|off", "status"
rem Leave IP blank to auto-discover from the diag broadcast.
title XRFD Remote Control (UDP 50998)
set "XRFD_IP="
set /p XRFD_IP=Arduino IP (blank = auto-discover):
set /p XRFD_CMD=Command (e.g. target 1 off):
call "%~dp0xrfd_send.bat" "%XRFD_CMD%"
pause
