@echo off
setlocal

set "PYTHON313=C:\Users\GPD\AppData\Local\Programs\Python\Python313\python.exe"

if not exist "%PYTHON313%" (
    echo [ERROR] Python 3.13 not found at:
    echo %PYTHON313%
    echo.
    echo Please update start_downloader.bat to point to your working Python 3.13 interpreter.
    exit /b 1
)

"%PYTHON313%" "%~dp0run_downloader.py"

endlocal
