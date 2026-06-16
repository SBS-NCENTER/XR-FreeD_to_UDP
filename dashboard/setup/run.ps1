# Starts the dashboard service (waitress). Invoked by server-manager via server.toml.
$root = Split-Path -Parent $PSScriptRoot         # dashboard/
Set-Location $root                                # so 'backend' is importable by -m
& "$root\.venv\Scripts\python.exe" -m backend.app
