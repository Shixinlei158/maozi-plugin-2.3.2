@echo off
cmd.exe /k "cd /d %~dp0 && py -3.9 -u -m ozon_pipeline.cli gui"