@echo off
rem sfm-mcp installer: creates .venv, hooks the bridge into SFM, registers with Claude Code.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 install.py %*
) else (
  python install.py %*
)
pause
