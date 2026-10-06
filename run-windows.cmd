@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python

echo Python 3 was not found. Install it from https://www.python.org/downloads/windows/
echo Then run this file again.
pause
exit /b 1

:use_py
py -3 "app\bitbucket_pr_ui.py"
if errorlevel 1 goto failed
exit /b 0

:use_python
python "app\bitbucket_pr_ui.py"
if errorlevel 1 goto failed
exit /b 0

:failed
echo The app could not start. Check Python 3 and Tkinter; see README.md.
pause
exit /b 1
