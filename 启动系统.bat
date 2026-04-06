@echo off
cd /d "D:\MyAI\My work team\Decided plan"
echo [SYSTEM] Starting watchdog window...
start "QuantAlpha Watchdog" C:\Python314\python.exe watchdog.py --discovery-start-delay 300
echo [SYSTEM] Waiting for UI port and opening browser...
start "" /min C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\MyAI\My work team\Decided plan\scripts\open_ui_when_ready.ps1"


