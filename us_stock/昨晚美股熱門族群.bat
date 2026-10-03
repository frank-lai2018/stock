@echo off
rem Double-click to show last night's US hot themes and the related Taiwan stocks.
rem Runs morning_us_hot.py: Python standard library only, prices from Yahoo Finance, no database needed.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python morning_us_hot.py %*
) else (
    py -3 morning_us_hot.py %*
)
echo.
pause
