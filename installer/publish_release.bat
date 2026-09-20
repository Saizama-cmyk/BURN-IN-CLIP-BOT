@echo off
rem Publish a new BURN-IN version to GitHub Releases (free). Every installed copy sees it
rem on its next start and offers "Update now"; the web installer always pulls the newest one.
rem   1. bump __version__ in clipbot\__init__.py
rem   2. set CLIPBOT_REPO=owner/name   (once)   and log in once:  gh auth login
rem   3. installer\publish_release.bat
setlocal
cd /d "%~dp0\.."
if "%CLIPBOT_REPO%"=="" (echo Set CLIPBOT_REPO=owner/name first. & exit /b 1)
where gh >nul 2>nul || (echo GitHub CLI missing: winget install GitHub.cli & exit /b 1)
gh auth status >nul 2>nul || (echo Sign in first: gh auth login & exit /b 1)
".venv\Scripts\python.exe" -m pytest -q || exit /b 1
call "%~dp0build_installer.bat" || exit /b 1
set /p CLIPBOT_VERSION=<installer\version.txt
gh release create "v%CLIPBOT_VERSION%" "dist\BURN-IN-Setup.exe" "dist\BURN-IN-WebSetup.exe" "dist\SHA256SUMS.txt" --repo "%CLIPBOT_REPO%" ^
  --title "BURN-IN %CLIPBOT_VERSION%" --notes-file installer\release_notes.md || exit /b 1
echo PUBLISHED v%CLIPBOT_VERSION% to https://github.com/%CLIPBOT_REPO%/releases
