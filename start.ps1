# Event Sync Service -- single-command start (Windows / PowerShell).
#
#   .\start.ps1
#
# Creates the Python virtualenv and installs both dependency sets on first run,
# then starts the API and the frontend together. Ctrl+C stops both.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venvPython = Join-Path $backend ".venv\Scripts\python.exe"

function Info($message) { Write-Host "==> $message" -ForegroundColor Cyan }

# --- Prerequisites --------------------------------------------------------
foreach ($tool in @("py", "npm")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Host "Missing '$tool'. Python 3.11+ and Node 18+ are required." -ForegroundColor Red
        exit 1
    }
}

# --- API port check -------------------------------------------------------
# If 8000 is taken, uvicorn exits but the UI still starts and proxies /api to
# whatever owns the port -- so stop here with a clear message instead.
$apiPort = 8000
$probe = New-Object System.Net.Sockets.TcpListener ([System.Net.IPAddress]::Loopback), $apiPort
try {
    $probe.Start()
    $probe.Stop()
}
catch {
    Write-Host "Port $apiPort is already in use, so the API cannot start." -ForegroundColor Red
    try {
        $owner = Get-NetTCPConnection -LocalPort $apiPort -State Listen -ErrorAction Stop | Select-Object -First 1
        $proc = Get-Process -Id $owner.OwningProcess -ErrorAction Stop
        Write-Host "It is held by PID $($proc.Id) ($($proc.ProcessName))." -ForegroundColor Red
        Write-Host "Stop it with:  Stop-Process -Id $($proc.Id)   then re-run .\start.ps1" -ForegroundColor Red
    }
    catch {
        Write-Host "Find the owner with:  netstat -ano | findstr :$apiPort   then stop that PID and re-run." -ForegroundColor Red
    }
    exit 1
}

# --- Backend setup --------------------------------------------------------
if (-not (Test-Path $venvPython)) {
    Info "Creating Python virtualenv"
    py -3 -m venv (Join-Path $backend ".venv")
    Info "Installing backend dependencies"
    & $venvPython -m pip install --quiet --upgrade pip
    & $venvPython -m pip install --quiet -r (Join-Path $backend "requirements.txt")
}

# --- Frontend setup -------------------------------------------------------
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Info "Installing frontend dependencies (first run only, may take a minute)"
    Push-Location $frontend
    npm install --no-audit --no-fund --silent
    Pop-Location
}

# --- Run both -------------------------------------------------------------
Info "Starting API on http://127.0.0.1:$apiPort  (docs at /docs)"
$api = Start-Process -FilePath $venvPython `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$apiPort" `
    -WorkingDirectory $backend -PassThru -NoNewWindow

try {
    Info "Starting UI -- open the 'Local:' URL Vite prints below (usually http://localhost:5173)"
    Info "Ctrl+C stops both processes."
    Push-Location $frontend
    npm run dev
}
finally {
    Pop-Location -ErrorAction SilentlyContinue
    if ($api -and -not $api.HasExited) {
        Info "Stopping API"
        Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
    }
}
