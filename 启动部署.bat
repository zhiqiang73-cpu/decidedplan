@echo off
cd /d "D:\MyAI\My work team\Decided plan"
echo [SYSTEM] Starting watchdog + monitor + discovery + UI ...
C:\Python314\python.exe scripts\reset_ui_port.py
start "" C:\Python314\pythonw.exe scripts\open_ui_when_ready.py
C:\Python314\python.exe watchdog.py


