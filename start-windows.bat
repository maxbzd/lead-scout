@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Scout

where py >nul 2>nul
if %errorlevel%==0 (set PYCMD=py -3) else (set PYCMD=python)
%PYCMD% --version >nul 2>nul
if errorlevel 1 (
  echo Не найден Python. Установите его с https://www.python.org/downloads/ и поставьте галочку "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: готовлю программу, это займёт пару минут...
  %PYCMD% -m venv .venv
  if errorlevel 1 ( echo Не получилось создать окружение. & pause & exit /b 1 )
)
if not exist ".venv\installed.flag" (
  .venv\Scripts\python -m pip install --quiet --require-hashes -r requirements.lock
  if errorlevel 1 ( echo Не получилось скачать нужные пакеты. Проверьте интернет и запустите снова. & pause & exit /b 1 )
  .venv\Scripts\python -m pip install --quiet --no-deps .
  if errorlevel 1 ( echo Не получилось установить программу. & pause & exit /b 1 )
  echo ok> .venv\installed.flag
)

.venv\Scripts\python -m leadscout --local
pause
