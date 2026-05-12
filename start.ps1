# Werewolf - Start Frontend + Backend (Single Window)
# Uses PowerShell background jobs to keep everything in one console.

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "================================================"
Write-Host "  Werewolf - AI Multi-Agent Game Platform"
Write-Host "  Launching Frontend + Backend"
Write-Host "================================================"
Write-Host ""

# ============================================================
# Check prerequisites
# ============================================================

Write-Host "[1/4] Checking Python..."
try {
    $null = Get-Command python -ErrorAction Stop
    Write-Host "       Python OK"
} catch {
    Write-Host "[ERROR] Python not found." -ForegroundColor Red
    pause
    exit 1
}

Write-Host "[2/4] Checking Node.js..."
try {
    $null = Get-Command node -ErrorAction Stop
    Write-Host "       Node.js OK"
} catch {
    Write-Host "[ERROR] Node.js not found." -ForegroundColor Red
    pause
    exit 1
}

Write-Host "[3/4] Checking backend..."
$backendDir = Join-Path $ScriptDir "ai_werewolf_core"
if (-not (Test-Path (Join-Path $backendDir "main.py"))) {
    Write-Host "[ERROR] $backendDir\main.py not found" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "       Backend OK"

Write-Host "[4/4] Checking frontend..."
$frontendDir = Join-Path $ScriptDir "frontend"
if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Write-Host "       Installing npm dependencies..."
    Push-Location $frontendDir
    npm install
    Pop-Location
}
Write-Host "       Frontend OK"

# ============================================================
# Launch services as background jobs
# ============================================================

Write-Host ""
Write-Host "------------------------------------------------"
Write-Host "  Starting services..."
Write-Host "------------------------------------------------"
Write-Host ""

# --- Backend (background job) ---
Write-Host "[Backend] Starting FastAPI on port 8000..."
$backendJob = Start-Job -Name "WerewolfBackend" -ScriptBlock {
    param($dir)
    Set-Location $dir
    uvicorn ai_werewolf_core.main:app --reload --host 0.0.0.0 --port 8000 2>&1
} -ArgumentList $ScriptDir

# --- Frontend (background job) ---
Write-Host "[Frontend] Starting Vite on port 5173..."
$frontendJob = Start-Job -Name "WerewolfFrontend" -ScriptBlock {
    param($dir)
    Set-Location $dir
    npm run dev 2>&1
} -ArgumentList $frontendDir

# Wait a moment for services to start
Start-Sleep -Seconds 4

Write-Host ""
Write-Host "================================================"
Write-Host "  Services started!"
Write-Host "================================================"
Write-Host ""
Write-Host "  Backend API docs : http://localhost:8000/docs"
Write-Host "  Frontend app     : http://localhost:5173"
Write-Host "  WebSocket        : ws://localhost:8000/ws/games/{game_id}"
Write-Host ""
Write-Host "  [Q] Quit   [L] Show backend logs   [F] Show frontend logs"
Write-Host "================================================"
Write-Host ""

# ============================================================
# Interactive control loop
# ============================================================
while ($true) {
    $key = [Console]::ReadKey($true)
    switch ($key.Key) {
        'Q' {
            Write-Host "`nStopping services..."
            Stop-Job -Name "WerewolfBackend" -ErrorAction SilentlyContinue
            Stop-Job -Name "WerewolfFrontend" -ErrorAction SilentlyContinue
            Remove-Job -Name "WerewolfBackend" -ErrorAction SilentlyContinue
            Remove-Job -Name "WerewolfFrontend" -ErrorAction SilentlyContinue
            Write-Host "All services stopped. Goodbye!"
            exit 0
        }
        'L' {
            Write-Host "`n--- Backend Logs ---"
            Receive-Job -Name "WerewolfBackend" | Write-Host
            Write-Host "--- End Backend Logs ---`n"
        }
        'F' {
            Write-Host "`n--- Frontend Logs ---"
            Receive-Job -Name "WerewolfFrontend" | Write-Host
            Write-Host "--- End Frontend Logs ---`n"
        }
    }
}
