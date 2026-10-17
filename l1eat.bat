@echo off
setlocal
chcp 65001 >nul
set "PY_EXE="
where python >nul 2>nul
if %errorlevel%==0 set "PY_EXE=python"
if not defined PY_EXE if exist "C:\Users\cid63\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY_EXE=C:\Users\cid63\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY_EXE (
    echo Python not found. Please install Python 3.10+ or edit PY path in this file.
    exit /b 1
)
"%PY_EXE%" "%~dp0l1eat.py" %*
exit /b %errorlevel%
