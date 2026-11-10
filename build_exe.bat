@echo off
rem Builds dist\NupExplorer.exe (single file, no console window) with PyInstaller.
setlocal
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean NupExplorer.spec
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo.
echo Done: %~dp0dist\NupExplorer.exe
pause
