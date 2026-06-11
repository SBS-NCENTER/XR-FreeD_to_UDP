@echo off
rem XRFD interactive shell - double-click to run.
rem Auto-discovers the device; type 'help' inside for commands.
title XRFD Shell
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0xrfd_shell.ps1" %*
pause
