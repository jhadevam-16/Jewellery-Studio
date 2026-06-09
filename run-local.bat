@echo off
REM ============================================================
REM  Design Innovation Studio - local development launcher
REM  Double-click this file, or run  .\run-local.bat  in a terminal.
REM  It sets up a virtual environment, installs dependencies,
REM  and starts the server at http://localhost:5000
REM ============================================================
cd /d "%~dp0"

REM -- Use an existing virtual environment, or create one --
if exist "venv\Scripts\activate.bat" (
    set "VENV=venv"
) else if exist ".venv\Scripts\activate.bat" (
    set "VENV=.venv"
) else (
    echo Creating virtual environment in .venv ...
    python -m venv .venv
    set "VENV=.venv"
)

call "%VENV%\Scripts\activate.bat"

echo.
echo Installing / updating dependencies ...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: dependency installation failed. See the messages above.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo ************************************************************
    echo  WARNING: .env not found.
    echo  Copy .env.example to .env and paste your API keys first:
    echo      copy .env.example .env
    echo ************************************************************
    echo.
)

echo.
echo Starting server on http://localhost:5000   (press CTRL+C to stop)
echo.
python server.py

pause
