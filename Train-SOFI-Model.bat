@echo off
cd /d "%~dp0"
python -m ml.train --data-dir data\raw
if errorlevel 1 (
  echo.
  echo Training did not complete. Check data\README.md and the terminal message above.
  pause
)
