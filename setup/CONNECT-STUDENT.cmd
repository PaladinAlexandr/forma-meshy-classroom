@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0connect-student.ps1"
set "forma_exit=%errorlevel%"
if not "%forma_exit%"=="0" pause
exit /b %forma_exit%
