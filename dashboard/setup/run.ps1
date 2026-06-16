# Starts the dashboard service (waitress). Invoked by server-manager via server.toml.
$root = Split-Path -Parent $PSScriptRoot         # dashboard/
Set-Location $root                                # so 'backend' is importable by -m
# -u (unbuffered) so startup URLs + logs stream live into server-manager's log view
& "$root\.venv\Scripts\python.exe" -u -m backend.app
