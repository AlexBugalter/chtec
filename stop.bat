@echo off
setlocal
if /i "%~1"=="/force" (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\stop_app.ps1" -Force
) else (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\stop_app.ps1"
)
if errorlevel 1 pause
