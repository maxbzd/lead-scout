@echo off
cd /d "%~dp0"
title Scout

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py -3) else (set PYCMD=python)
%PYCMD% --version >nul 2>nul
if errorlevel 1 goto nopython

if not exist ".venv\Scripts\python.exe" (
  echo First start: preparing the program, this takes a couple of minutes...
  %PYCMD% -m venv .venv
  if errorlevel 1 goto fail
)
if not exist ".venv\installed.flag" (
  .venv\Scripts\python -m pip install --require-hashes -r requirements.lock
  if errorlevel 1 goto fail
  .venv\Scripts\python -m pip install --no-deps .
  if errorlevel 1 goto fail
  echo ok> .venv\installed.flag
)

.venv\Scripts\python -m leadscout --local
pause
exit /b 0

:nopython
echo Python was not found. Install Python 3.11 - 3.13 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer. Then run this file again.
pause
exit /b 1

:fail
echo.
echo Setup failed. Read the error above (usually no internet, or an unsupported Python version).
echo Python 3.12 or 3.13 is the safest. If you want to retry, delete the .venv folder and run this file again.
pause
exit /b 1
