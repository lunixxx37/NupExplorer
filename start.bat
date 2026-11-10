@echo off
rem Starts NUP Explorer (needs Python 3.14 with the packages from requirements.txt).
rem Optional: path to the .nup file as parameter, e.g.  start.bat D:\Games\game.nup
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Use NupExplorer.exe or install Python 3.14.
  pause
  exit /b 1
)
python app.py %*
if errorlevel 1 pause
