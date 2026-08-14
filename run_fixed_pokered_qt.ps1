$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Launcher = Join-Path $Root "run_fixed_pokered.py"

& $Python $Launcher @args
