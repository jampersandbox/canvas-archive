@echo off
title Canvas Archive Setup
echo.
echo ==========================================
echo    Canvas Archive - Windows Setup
echo ==========================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Please install Python from https://python.org/downloads
    echo IMPORTANT: Tick "Add Python to PATH" during installation!
    start https://python.org/downloads
    pause
    exit /b 1
)

echo Found: 
python --version
echo.

:: Virtual environment
echo Setting up virtual environment...
python -m venv venv
call venv\Scripts\activate.bat

:: Install packages
echo Installing required packages...
pip install --quiet --upgrade pip
pip install --quiet requests tqdm playwright yt-dlp

echo Downloading browser (this may take a few minutes)...
playwright install chromium

:: Patch scripts
echo Configuring scripts...
python patch_scripts.py

:: Create launcher
echo @echo off                           > "Launch Canvas Archive.bat"
echo title Canvas Archive               >> "Launch Canvas Archive.bat"
echo cd /d "%%~dp0"                     >> "Launch Canvas Archive.bat"
echo call venv\Scripts\activate.bat     >> "Launch Canvas Archive.bat"
echo python canvas_archive.py           >> "Launch Canvas Archive.bat"
echo pause                              >> "Launch Canvas Archive.bat"

echo.
echo ==========================================
echo    Setup complete!
echo.
echo    Double-click "Launch Canvas Archive.bat"
echo    to start the app.
echo ==========================================
echo.
pause