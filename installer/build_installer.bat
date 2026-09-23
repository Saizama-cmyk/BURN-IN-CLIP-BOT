@echo off
rem Build the installers into dist\:
rem   Ashvane-Setup-<version>.exe   full installer (also copied without the version,
rem                                          the fixed name each GitHub release carries)
rem   Ashvane-WebSetup.exe  tiny web installer (needs CLIPBOT_REPO=owner/name)
setlocal
cd /d "%~dp0\.."
set "PY=.venv\Scripts\python.exe"
call "%~dp0..\build.bat" || goto :fail
cd /d "%~dp0\.."
set /p CLIPBOT_VERSION=<installer\version.txt
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
  echo Inno Setup 6 not found. Install it free: winget install JRSoftware.InnoSetup
  goto :fail
)
"%ISCC%" /Q installer\Ashvane.iss || goto :fail
copy /y "dist\Ashvane-Setup-%CLIPBOT_VERSION%.exe" "dist\Ashvane-Setup.exe" >nul || goto :fail

echo INSTALLER OK: dist\Ashvane-Setup-%CLIPBOT_VERSION%.exe
if "%CLIPBOT_REPO%"=="" (
  echo Web installer skipped: set CLIPBOT_REPO=owner/name to build it.
) else (
  "%ISCC%" /Q installer\WebSetup.iss || goto :fail
  echo WEB INSTALLER OK: dist\Ashvane-WebSetup.exe
)
"%PY%" installer\release_checksums.py || goto :fail
exit /b 0

:fail
echo INSTALLER FAILED
exit /b 1
