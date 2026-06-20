@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_account_prep.ps1" -ShowGame %*
set EXITCODE=%ERRORLEVEL%
echo.
echo [account-prep] finished with exit code %EXITCODE%
if /i not "%ACCOUNT_PREP_NO_PAUSE%"=="1" pause
exit /b %EXITCODE%
