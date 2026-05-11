@echo off
REM Daily research scan. Invoked by Windows Task Scheduler.
REM Logs go to logs\daily-YYYY-MM-DD.log next to this file.

setlocal

REM cd to this script's own directory so relative paths (research.db, logs\) work.
cd /d "%~dp0"

REM Make sure logs\ exists.
if not exist "logs" mkdir "logs"

REM Datestamped log file: logs\daily-2026-05-11.log
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set DT=%%I
set LOGDATE=%DT:~0,4%-%DT:~4,2%-%DT:~6,2%
set LOGFILE=logs\daily-%LOGDATE%.log

echo. >> "%LOGFILE%"
echo ===== Run started %DATE% %TIME% ===== >> "%LOGFILE%"

REM Load .env if present (KEY=VALUE lines).
if exist ".env" (
    for /f "usebackq tokens=* delims=" %%L in (".env") do (
        echo %%L | findstr /b "#" >nul
        if errorlevel 1 (
            echo %%L | findstr "=" >nul
            if not errorlevel 1 set "%%L"
        )
    )
)

REM Run the scan. Adjust `python` to `py -3` or full path if needed.
python run.py --quiet >> "%LOGFILE%" 2>&1
set EXITCODE=%ERRORLEVEL%

echo ===== Run finished %DATE% %TIME% (exit %EXITCODE%) ===== >> "%LOGFILE%"

endlocal & exit /b %EXITCODE%
