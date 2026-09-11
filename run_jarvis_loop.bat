@echo off
REM ============================================================
REM  Auto-restart watchdog for main.py.
REM  Runs main.py in a loop -- if it ever exits (crash, unhandled
REM  exception, killed, whatever), it's restarted automatically
REM  after a short pause instead of just leaving JARVIS dead.
REM
REM  Not meant to be double-clicked directly -- start_jarvis.bat
REM  launches this in its own window. To stop JARVIS for real,
REM  close that window (or Ctrl+C here, then answer N to "Terminate
REM  batch job?" is wrong -- answer Y).
REM
REM  Every restart is appended to logs\crash.log with a timestamp,
REM  so you can tell a genuinely crash-prone setup from one that's
REM  just been running fine for days.
REM ============================================================
setlocal enabledelayedexpansion
cd /d %~dp0

if not exist logs mkdir logs

:loop
echo.
echo [%date% %time%] Starting main.py...
py -3.11 main.py
set EXITCODE=%errorlevel%

if %EXITCODE%==0 (
    echo [%date% %time%] main.py exited normally ^(code 0^) -- not restarting.
    echo [%date% %time%] Clean exit, code 0 >> logs\crash.log
    goto :end
)

echo [%date% %time%] main.py exited with code %EXITCODE% -- restarting in 5 seconds...
echo [%date% %time%] Crashed, exit code %EXITCODE% -- restarting >> logs\crash.log
timeout /t 5 >nul
goto :loop

:end
echo.
echo JARVIS has stopped. Close this window or press any key.
pause >nul
