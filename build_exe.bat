@echo off
REM Builds ONE shareable file: release\SpinVid-windows.exe (Python, Pillow and FFmpeg inside).
setlocal
cd /d "%~dp0"
set "PY="
for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('py -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY (
  echo Could not find Python. Install it from https://www.python.org/downloads/
  pause & exit /b 1
)
echo Using Python: %PY%
"%PY%" -m pip install --upgrade -r requirements.txt pyinstaller imageio-ffmpeg || (pause & exit /b 1)
"%PY%" build.py || (
  echo.
  echo The build failed. If it says "cannot execute", Windows security
  echo ^(Smart App Control / antivirus^) may be blocking PyInstaller.
  echo You can build in the cloud instead - see README.md, "Build on GitHub".
  pause & exit /b 1
)
echo.
echo Done! Send your friends: %CD%\release\SpinVid-windows.exe
explorer "%CD%\release"
pause
