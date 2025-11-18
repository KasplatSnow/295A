@echo off
REM Quick Start Script for UI_ver_1 with AI Integration (Windows)
REM This script helps you get the system running quickly on Windows

setlocal enabledelayedexpansion

echo ==========================================
echo   System Setup
echo ==========================================
echo.

REM Check if we're in the right directory
@REM if not exist "ai" (
@REM     echo [X] Error: Please run this script from the UI_ver_1 directory
@REM     pause
@REM     exit /b 1
@REM )
if not exist "server" (
    echo [X] Error: Please run this script from the UI_ver_1 directory
    pause
    exit /b 1
)

REM Step 1: Install Node dependencies
echo [*] Step 1: Installing Node.js dependencies...
if not exist "node_modules" (
    call npm install
    if errorlevel 1 (
        echo [X] Failed to install Node dependencies
        pause
        exit /b 1
    )
) else (
    echo [+] Node modules already installed
)
echo.

@REM REM Step 2: Check Python environment (commented out - can be enabled if needed)
@REM REM echo [*] Step 2: Checking Python environment...
@REM REM if not exist "ai\.venv" (
@REM REM     echo Creating Python virtual environment...
@REM REM     cd ai
@REM REM     python -m venv .venv
@REM REM     call .venv\Scripts\activate.bat
@REM REM     echo Installing Python dependencies...
@REM REM     pip install -r requirements.txt
@REM REM     cd ..
@REM REM     echo [+] Python environment created
@REM REM ) else (
@REM REM     echo [+] Python environment already exists
@REM REM )
@REM REM echo.

@REM REM Step 3: Check for AI models (commented out - can be enabled if needed)
@REM REM echo [*] Step 3: Checking AI models...
@REM REM if not exist "ai\yolov8n.pt" (
@REM REM     echo [!] YOLOv8 model not found!
@REM REM     echo    Downloading yolov8n.pt...
@REM REM     cd ai
@REM REM     call .venv\Scripts\activate.bat
@REM REM     python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
@REM REM     cd ..
@REM REM )
@REM REM echo [+] YOLOv8 model ready
@REM REM echo.

@REM REM Step 4: Check gallery (commented out - can be enabled if needed)
@REM REM echo [*] Step 4: Checking entity gallery...
@REM REM if exist "ai\gallery\bob\bob.npy" (
@REM REM     echo [+] Entity 'bob' registered
@REM REM ) else (
@REM REM     echo [!] No entities registered in gallery
@REM REM     echo    To add entities, see AI_INTEGRATION.md
@REM REM )
@REM REM echo.

REM Display options
echo ==========================================
echo   Setup Complete! Choose how to start:
echo ==========================================
echo.
echo Start Web UI Only ()
echo   npm run dev
echo.
@REM echo Option 2: Start Web UI + AI Detection (process new video)
@REM echo   Terminal 1: npm run dev
@REM echo   Terminal 2: cd ai ^&^& .venv\Scripts\activate ^&^& python -m src.run_local --source ^<video^> --headless --save-incidents
@REM echo.
@REM echo Option 3: Test AI System Only (no web UI)
@REM echo   cd ai ^&^& .venv\Scripts\activate ^&^& python -m src.run_local --source ^<video^> --show
echo.

REM Offer to start dev server
set /p START_SERVER="Would you like to start the web UI now? (Y/N): "
if /i "%START_SERVER%"=="Y" (
    echo.
    echo Starting development server...
    echo    Web UI will be available at http://localhost:5000
    echo    Press Ctrl+C to stop
    echo.
    call npm run dev
) else (
    echo.
    echo To start manually, run: npm run dev
    echo.
    pause
)

endlocal
