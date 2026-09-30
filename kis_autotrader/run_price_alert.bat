@echo off
REM Register in Task Scheduler: weekdays 07:59, run this file.
cd /d "%~dp0"
if not exist logs mkdir logs
python price_alert.py --loop >> logs\price_alert.log 2>&1
