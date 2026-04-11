@echo off
cd /d "D:\MyAI\My work team\Decided plan"

echo [SYSTEM] Stopping any existing watchdog/monitor processes...
taskkill /F /FI "WINDOWTITLE eq QuantAlpha*" >nul 2>&1

REM Kill any python process running watchdog.py or run_monitor.py
wmic process where "name='python.exe' and commandline like '%%watchdog%%'" delete >nul 2>&1
wmic process where "name='pythonw.exe' and commandline like '%%watchdog%%'" delete >nul 2>&1
wmic process where "name='python.exe' and commandline like '%%run_monitor%%'" delete >nul 2>&1
wmic process where "name='pythonw.exe' and commandline like '%%run_monitor%%'" delete >nul 2>&1

REM Remove stale lock file
if exist "monitor\output\watchdog.lock" del /F /Q "monitor\output\watchdog.lock" >nul 2>&1

REM Brief pause to let OS release file handles
timeout /t 2 /nobreak >nul

echo [SYSTEM] Starting watchdog...
if exist C:\Python314\pythonw.exe (
  start "" C:\Python314\pythonw.exe watchdog.py --discovery-start-delay 60
) else if exist "C:\Users\GPD\AppData\Local\Programs\Python\Python313\python.exe" (
  start "QuantAlpha Watchdog" "C:\Users\GPD\AppData\Local\Programs\Python\Python313\python.exe" watchdog.py --discovery-start-delay 60
) else (
  start "QuantAlpha Watchdog" python.exe watchdog.py --discovery-start-delay 60
)

echo [SYSTEM] Waiting for UI port and opening browser...
start "" /min C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\MyAI\My work team\Decided plan\scripts\open_ui_when_ready.ps1"
