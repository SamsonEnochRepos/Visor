@echo off
setlocal enabledelayedexpansion

echo.
echo  ======================================
echo   VISOR - Touchless OS Control System
echo   Installer for Windows
echo  ======================================
echo.

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.8+ from python.org
    pause
    exit /b 1
)

:: Check Python version
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Found Python %PYVER%

:: Create virtual environment
if not exist "venv" (
    echo [STEP 1/5] Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
) else (
    echo [STEP 1/5] Virtual environment already exists
)

:: Activate venv
call venv\Scripts\activate.bat

:: Install dependencies
echo [STEP 2/5] Installing Python dependencies...
pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARNING] Some packages may have failed. Trying PyAudio from alternative...
    pip install pyaudio 2>nul || (
        echo [NOTE] PyAudio install failed. You may need to install it manually:
        echo        pip install pipwin ^&^& pipwin install pyaudio
    )
)
echo [OK] Dependencies installed

:: Download Vosk model (extracted into models\)
set MODEL_DIR=models\vosk-model-small-en-us-0.15
set MODEL_ZIP=vosk-model-small-en-us-0.15.zip
set MODEL_URL=https://alphacephei.com/vosk/models/%MODEL_ZIP%

if not exist "models" mkdir models

if not exist "%MODEL_DIR%" (
    echo [STEP 3/5] Downloading Vosk speech model ^(~40MB^)...

    :: Try PowerShell download
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%MODEL_URL%' -OutFile '%MODEL_ZIP%'}" 2>nul

    if exist "%MODEL_ZIP%" (
        echo [OK] Model downloaded. Extracting...
        powershell -Command "Expand-Archive -Path '%MODEL_ZIP%' -DestinationPath 'models' -Force"
        del "%MODEL_ZIP%"
        echo [OK] Model extracted to %MODEL_DIR%
    ) else (
        echo [WARNING] Could not download model automatically.
        echo           Please download manually from:
        echo           %MODEL_URL%
        echo           Extract to this directory.
    )
) else (
    echo [STEP 3/5] Vosk model already present
)

:: Create VBS launcher (runs pythonw, no console window)
echo [STEP 4/5] Creating silent launcher...
set SCRIPT_DIR=%~dp0
(
    echo Set WshShell = CreateObject^("WScript.Shell"^)
    echo WshShell.CurrentDirectory = "%SCRIPT_DIR%"
    echo WshShell.Run """%SCRIPT_DIR%venv\Scripts\pythonw.exe"" ""%SCRIPT_DIR%main.py""", 0, False
) > VISOR.vbs
echo [OK] VISOR.vbs created (double-click to launch silently)

:: Create desktop shortcut
echo [STEP 5/5] Creating desktop shortcut...
set DESKTOP=%USERPROFILE%\Desktop
powershell -Command "& {$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\VISOR.lnk'); $s.TargetPath = '%SCRIPT_DIR%VISOR.vbs'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'VISOR Touchless OS Control'; $s.Save()}" 2>nul

if exist "%DESKTOP%\VISOR.lnk" (
    echo [OK] Desktop shortcut created
) else (
    echo [NOTE] Could not create shortcut. Use VISOR.vbs to launch.
)

echo.
echo  ======================================
echo   Installation complete!
echo.
echo   To start VISOR:
echo     - Double-click VISOR.vbs
echo     - Or: venv\Scripts\python main.py
echo.
echo   VISOR runs in the system tray.
echo   Right-click the tray icon for options.
echo  ======================================
echo.
pause
