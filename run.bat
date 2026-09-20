@echo off
rem Dev launcher. run.bat            -> desktop window + tray
rem              run.bat --console  -> console mode, dashboard at http://127.0.0.1:8787
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  .venv\Scripts\python.exe -m clipbot --window
) else (
  .venv\Scripts\python.exe -m clipbot %*
)
