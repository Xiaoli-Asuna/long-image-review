@echo off
cd /d "%~dp0"
title Review Office Server
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0StartServer.ps1"
if errorlevel 1 pause
