@echo off
REM Double-click to start UN Validator (uses .venv if present).
REM Data folder: .\data by default. To use another one, e.g.:  set UNV_DATA_ROOTS=C:\data
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=python
)
start "" http://127.0.0.1:8765/
%PY% run.py serve
pause
