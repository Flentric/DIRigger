@echo off
rem Put an auto-rigged model into Dead Island level .rpack files.
rem   install.bat out_hero_logan "C:\...\Dead Island\DI\Data"
rem   install.bat out_hero_logan hotel_PC.rpack other_PC.rpack
rem Originals are kept as .rpack.bak the first time.
setlocal
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    pause
    exit /b 1
)
if "%~2"=="" (
    echo Usage: install.bat out_hero_logan  level.rpack ^| folder-of-rpacks ...
    pause
    exit /b 0
)
%PY% -m dirigger install %*
pause
