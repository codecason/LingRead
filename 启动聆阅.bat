@echo off
rem Launch the self-contained release; no Python installation required.
if not exist "%~dp0dist\LingRead.exe" (
    echo Missing dist\LingRead.exe. Please rebuild with LingReadStandalone.spec.
    pause
    exit /b 1
)
start "" "%~dp0dist\LingRead.exe"
exit /b 0
