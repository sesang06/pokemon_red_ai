@echo off
set SCRIPT_DIR=%~dp0
"%SCRIPT_DIR%.venv\Scripts\python.exe" "%SCRIPT_DIR%run_pokemon_play.py" %*
