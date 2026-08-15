param(
    [int]$Port = 8000
)

$projectRoot = $PSScriptRoot
$databasePath = (Join-Path $projectRoot "data\adk_sessions.db").Replace("\", "/")
$adkExecutable = Join-Path $projectRoot ".venv\Scripts\adk.exe"

& $adkExecutable web `
    --port $Port `
    --no-reload `
    --session_service_uri "sqlite:///$databasePath" `
    (Join-Path $projectRoot "src\pokemon_agent\adk_agent")
