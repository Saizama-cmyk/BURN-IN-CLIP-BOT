@echo off
rem Install the current BURN-IN build using the same tested upgrade path as releases.
rem Usage: install.bat [/quiet]   (run installer\build_installer.bat first)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\manage_install.ps1" -Action Install %*
exit /b %errorlevel%

