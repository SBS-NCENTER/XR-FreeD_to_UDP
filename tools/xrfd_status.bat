@echo off
rem Double-click: query device status (auto-discovers IP).
title XRFD - status
call "%~dp0xrfd_send.bat" "status"
pause
