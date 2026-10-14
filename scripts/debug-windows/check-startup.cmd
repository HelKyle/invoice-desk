@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "probe_root=%~dp0"
set "probe_engine=%probe_root%resources\engine"
echo Script folder: "%probe_root%"
if not exist "%probe_engine%\python.exe" goto missing_files
if not exist "%probe_engine%\python312.dll" goto missing_files
if not exist "%probe_engine%\app\desktop_engine.py" goto missing_files
pushd "%probe_engine%"
if errorlevel 1 goto bad_directory
set "INVOICE_DEBUG_DIR=%LOCALAPPDATA%\InvoiceDesk-Debug-Probe\%RANDOM%-%RANDOM%"
set "PLAYWRIGHT_BROWSERS_PATH=%probe_engine%\browsers"
set "OMP_NUM_THREADS=2"
mkdir "%INVOICE_DEBUG_DIR%"
if errorlevel 1 (
  echo Cannot create debug log directory.
  popd
  pause
  exit /b 1
)
echo Checking the same subprocess and OCR initialization used by the app.
echo No Excel file or tax-platform request is needed.
echo Logs: "%INVOICE_DEBUG_DIR%"
echo Python stack traces are saved every 30 seconds while running.
echo Please wait up to 90 seconds for OCR initialization.
"%probe_engine%\python.exe" -u -X faulthandler "%probe_engine%\app\desktop_engine.py" startup-test >"%INVOICE_DEBUG_DIR%\console.log" 2>&1
set "probe_result=%errorlevel%"
popd
type "%INVOICE_DEBUG_DIR%\console.log"
echo.
echo Exit code: %probe_result%
echo Logs: "%INVOICE_DEBUG_DIR%"
pause
exit /b %probe_result%

:missing_files
echo.
echo ERROR: The extracted app runtime was not found beside this script.
echo Expected Python: "%probe_engine%\python.exe"
echo Expected DLL: "%probe_engine%\python312.dll"
echo Expected entry: "%probe_engine%\app\desktop_engine.py"
echo Extract the ENTIRE ZIP first, then run check-startup.cmd from that folder.
echo Do not run this script inside the ZIP or move it out of the app folder.
echo InvoiceDesk-Debug.exe and the resources folder must be beside this script.
echo Python has not been started. This is a folder layout error, not an OCR result.
pause
exit /b 2

:bad_directory
echo ERROR: Cannot access engine directory: "%probe_engine%"
pause
exit /b 2
