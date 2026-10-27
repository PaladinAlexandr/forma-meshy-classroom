@echo off
setlocal
cd /d "%~dp0"
if not exist ".env.classroom" (
    echo Create .env.classroom from .env.classroom.example first.
    exit /b 1
)
docker compose --env-file .env.classroom -f compose.classroom.yaml %*
exit /b %errorlevel%
