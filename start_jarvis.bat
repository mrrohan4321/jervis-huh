@echo off
REM ============================================================
REM  One-click JARVIS launcher.
REM  Double-click this file (or make a desktop shortcut to it) --
REM  it starts main.py (via run_jarvis_loop.bat, which auto-restarts
REM  it if it ever crashes) in its own window, waits until the
REM  backend answers /health, then opens the HUD frontend in your
REM  browser. Close the "JARVIS Backend" window to stop everything.
REM ============================================================
setlocal

cd /d %~dp0

echo Starting JARVIS backend...
start "JARVIS Backend" cmd /k run_jarvis_loop.bat

echo Waiting for the backend to come online...
:waitloop
timeout /t 1 >nul
curl -s http://localhost:8000/health >nul 2>&1
if errorlevel 1 goto waitloop

echo Backend is up.

set FRONTEND=
if exist "%~dp0frontend\index.html" set FRONTEND=%~dp0frontend\index.html
if "%FRONTEND%"=="" if exist "%~dp0files (6)\index.html" set FRONTEND=%~dp0files (6)\index.html

if "%FRONTEND%"=="" (
    echo Couldn't find index.html in a "frontend" or "files (6)" folder next to this script.
    echo Open it manually, or edit start_jarvis.bat and point FRONTEND at the right path.
) else (
    echo Opening JARVIS HUD...
    start "" "%FRONTEND%"
)

echo.
echo All set. This window can stay open or be closed --
echo the backend keeps running in the "JARVIS Backend" window.
pause
