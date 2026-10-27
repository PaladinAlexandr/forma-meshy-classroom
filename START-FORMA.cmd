@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\start-forma.ps1" %*
set "forma_exit=%errorlevel%"
echo.
pause
exit /b %forma_exit%
