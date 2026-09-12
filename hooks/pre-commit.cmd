@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

for /f "delims=" %%i in ('git rev-parse --show-toplevel 2^>nul') do set "REPO_ROOT=%%i"
if "%REPO_ROOT%"=="" exit /b 0

set "PYTHONPATH=%REPO_ROOT%\src;%PYTHONPATH%"
python -m lucid_memories.core.privacy_gate
if errorlevel 1 exit /b 1
exit /b 0
