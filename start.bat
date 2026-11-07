@echo off
cd /d "%~dp0"
echo Installing deps...
py -m pip install -r requirements.txt
echo Starting bot...
py bot.py
pause
