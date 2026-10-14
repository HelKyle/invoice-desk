@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "probe_root=%~dp0"
if not exist "%probe_root%InvoiceDesk-Debug.exe" goto missing_files
if not exist "%probe_root%resources\engine\python.exe" goto missing_files
set "probe_data=%LOCALAPPDATA%\InvoiceDesk-Debug-Electron-Probe\%RANDOM%-%RANDOM%"
set "INVOICE_APP_DATA=%probe_data%\open-stdin"
echo Test 1: actual Electron parent, piped stdio, windowsHide, embedded isolated Python.
echo Log directory: "%INVOICE_APP_DATA%\debug-logs"
start "" /wait "%probe_root%InvoiceDesk-Debug.exe" --startup-test
set "open_result=%errorlevel%"
echo Test 1 exit code: %open_result%
set "INVOICE_APP_DATA=%probe_data%\closed-stdin"
echo Test 2: same launch conditions, with stdin immediately closed.
echo Log directory: "%INVOICE_APP_DATA%\debug-logs"
start "" /wait "%probe_root%InvoiceDesk-Debug.exe" --startup-test-close-stdin
set "closed_result=%errorlevel%"
echo Test 2 exit code: %closed_result%
echo Each session contains startup-probe.json with an explicit ok result.
echo Please provide this entire log directory: "%probe_data%"
pause
if not "%open_result%"=="0" exit /b 1
exit /b %closed_result%

:missing_files
echo ERROR: Extract the entire ZIP first. Run this script beside InvoiceDesk-Debug.exe and resources.
echo Script directory: "%probe_root%"
pause
exit /b 2
