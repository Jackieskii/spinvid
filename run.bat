@echo off
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Python is not installed. Get it from https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" during install, then run this again.
  pause & exit /b 1
)
python -c "import PIL" 2>nul || python -m pip install -r requirements.txt
where ffmpeg >nul 2>nul || if not exist "ffmpeg\ffmpeg.exe" if not exist "ffmpeg\bin\ffmpeg.exe" (
  echo FFmpeg not found - installing it with winget...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  echo.
  echo If FFmpeg was just installed, close this window and run run.bat again.
  pause
)
start "" pythonw spinvid.py
