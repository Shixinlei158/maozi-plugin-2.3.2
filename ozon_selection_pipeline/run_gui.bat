@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================
echo  Ozon Selection Pipeline GUI
echo ================================
echo.
echo Starting GUI, please wait...
echo.
py -3.9 -u -m ozon_pipeline.cli gui
echo.
echo ===== GUI closed =====
pause