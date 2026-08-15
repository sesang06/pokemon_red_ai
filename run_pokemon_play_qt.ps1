$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Launcher = Join-Path $Root "run_pokemon_play.py"

& $Python $Launcher @args
