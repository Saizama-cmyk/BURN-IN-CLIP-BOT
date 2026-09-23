@echo off
rem Lets the BURN-IN phone app reach this PC.
rem
rem Windows Firewall silently drops incoming connections to the dashboard, so the phone can see
rem nothing even when everything else is right. This adds one rule for port 8787 on private
rem (home) networks only - never on public Wi-Fi. Double-click it and say yes to the prompt.
rem
rem The installer runs it with /quiet, which skips the closing prompt: there would be no window
rem to press a key in, so it would wait for a keypress that never comes.
setlocal
set PORT=8787
set RULE=BURN-IN phone remote
set QUIET=%1

net session >nul 2>&1
if errorlevel 1 (
  echo Asking for administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%QUIET%' -Verb RunAs"
  exit /b
)

netsh advfirewall firewall delete rule name="%RULE%" >nul 2>&1
netsh advfirewall firewall add rule name="%RULE%" dir=in action=allow protocol=TCP ^
  localport=%PORT% profile=private,domain ^
  description="Lets the BURN-IN phone app and browsers on your own network reach the dashboard."
if errorlevel 1 (
  echo.
  echo Could not add the rule. Run this file as administrator.
) else (
  echo.
  echo Done. Your phone can now reach BURN-IN on port %PORT% over your own network.
  echo In BURN-IN: Settings - Dashboard - Phone remote, then use the address it shows.
)
echo.
if /i not "%QUIET%"=="/quiet" pause
