@echo off
echo ============================================
echo  Wireless Stats Service - Windows Installer
echo ============================================
echo.

:: Check for admin privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: This script must be run as Administrator!
    echo Right-click and select "Run as administrator"
    pause
    exit /b 1
)

:: Check Python
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Python not found. Install Python 3.10+ from python.org
    pause
    exit /b 1
)

echo [1/3] Installing Python dependencies...
pip install -r "%~dp0requirements.txt"
if %errorLevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [2/3] Installing Windows service...
python "%~dp0wireless_service_win.py" install
if %errorLevel% neq 0 (
    echo ERROR: Failed to install service
    pause
    exit /b 1
)

echo.
echo [3/3] Starting service...
python "%~dp0wireless_service_win.py" start
if %errorLevel% neq 0 (
    echo WARNING: Failed to start service. Try starting manually:
    echo   net start WirelessStatsService
)

echo.
echo ============================================
echo  Installation complete!
echo.
echo  Service: WirelessStatsService
echo  Syslog:  Listening on UDP port 514
echo  API:     http://localhost:8088
echo.
echo  API Endpoints:
echo    http://localhost:8088/api/today
echo    http://localhost:8088/api/date/2026-03-02
echo    http://localhost:8088/api/month/2026-03
echo    http://localhost:8088/api/unique-users
echo    http://localhost:8088/api/unique-users?days=30
echo.
echo  Manage service:
echo    net stop WirelessStatsService
echo    net start WirelessStatsService
echo    python wireless_service_win.py remove
echo ============================================
pause
