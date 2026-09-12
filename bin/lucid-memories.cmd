@echo off
setlocal

if defined LUCID_MEMORIES_ROOT (
  set "ROOT=%LUCID_MEMORIES_ROOT%"
) else if exist "%~dp0..\src\lucid_memories" (
  pushd "%~dp0.."
  set "ROOT=%CD%"
  popd
) else (
  set "ROOT=%USERPROFILE%\.cursor\lucid-memories"
)

set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" (
  set "PY=%LOCALAPPDATA%\Python\bin\python.exe"
)
if not exist "%PY%" (
  set "PY=python"
)

set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%PY%" -m lucid_memories.entrypoints.cli %*
