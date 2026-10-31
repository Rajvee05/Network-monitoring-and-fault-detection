@echo off
cd /d "%~dp0"
python SOFI-Replay.py
if errorlevel 1 (
  echo.
  echo Could not start the SOFI replay. Check Python and the data/raw folder.
  pause
)
