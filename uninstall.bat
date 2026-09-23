@echo off
rem Use the registered Ashvane uninstaller; user data is kept unless you choose to remove it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\manage_install.ps1" -Action Uninstall %*
exit /b %errorlevel%

