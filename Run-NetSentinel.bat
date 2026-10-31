@echo off
cd /d "%~dp0"
python NetSentinel.py
if errorlevel 1 (
  echo.
  echo Could not start NetSentinel. Install Python 3 with Tkinter enabled and try again.
  pause
)
