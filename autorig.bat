@echo off
rem DIRigger auto-rigger. Drag an .obj onto this file, or run:
rem   autorig.bat model.obj [--template path\to\hero_logan.msh] [--texture head=face.png ...]
rem Run "autorig.bat --help" for all options.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

%PY% -c "import numpy" >nul 2>nul
if errorlevel 1 (
    echo Installing numpy...
    %PY% -m pip install --user numpy || (echo Could not install numpy. & pause & exit /b 1)
)

if "%~1"=="" (
    %PY% autorig.py --help
    pause
    exit /b 0
)

%PY% autorig.py %*
if errorlevel 1 (
    echo.
    echo Auto-rig failed, see the message above.
)
pause
