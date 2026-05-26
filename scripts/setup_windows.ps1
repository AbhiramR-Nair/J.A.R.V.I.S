# research-jarvis: one-shot Windows setup
# Run from repo root in PowerShell 5.1:
#   .\scripts\setup_windows.ps1
#
# Prerequisites (install manually before running):
#   - Python 3.12+   https://python.org
#   - Node.js LTS    https://nodejs.org
#   - Git            https://git-scm.com

# PowerShell 5.1 defaults to CP1252; force UTF-8 so Gemini's em dashes,
# smart quotes, and Unicode tool output render correctly in the console.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "Stop"

Write-Host "=== research-jarvis: Windows setup ===" -ForegroundColor Cyan
Write-Host ""

# -- 1. Sanity checks --------------------------------------------------------

Write-Host "Checking prerequisites..." -ForegroundColor Yellow

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[error] Python not found. Install from https://python.org and rerun." -ForegroundColor Red
    exit 1
}
$pyVersion = python --version
Write-Host "  [ok] $pyVersion"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "[error] Node.js not found. Install LTS from https://nodejs.org and rerun." -ForegroundColor Red
    exit 1
}
$nodeVersion = node --version
Write-Host "  [ok] Node $nodeVersion"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "[error] npm not found. It should ship with Node - check your Node install." -ForegroundColor Red
    exit 1
}
Write-Host "  [ok] npm $(npm --version)"

Write-Host ""

# -- 2. Python virtual environment -------------------------------------------

Write-Host "Setting up Python virtual environment..." -ForegroundColor Yellow

if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Host "  [ok] .venv created"
} else {
    Write-Host "  [skip] .venv already exists"
}

# Activate for the rest of this script
& .venv\Scripts\Activate.ps1

Write-Host "  Installing backend dependencies (this may take a few minutes)..."
pip install -r backend\requirements.txt --quiet
Write-Host "  [ok] Backend dependencies installed"

Write-Host ""

# -- 3. Frontend dependencies ------------------------------------------------

Write-Host "Installing frontend dependencies..." -ForegroundColor Yellow

Push-Location frontend
npm install --silent
Pop-Location
Write-Host "  [ok] npm install complete"

Write-Host ""

# -- 4. Runtime data directories ---------------------------------------------

Write-Host "Creating runtime directories..." -ForegroundColor Yellow

$dirs = @("data\logs", "data\recordings", "data\chroma")
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
        Write-Host "  [ok] $d"
    } else {
        Write-Host "  [skip] $d already exists"
    }
}

Write-Host ""

# -- 5. Environment file -----------------------------------------------------

Write-Host "Checking .env..." -ForegroundColor Yellow

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "  [ok] .env created from .env.example"
    Write-Host "  [!!] Fill in your API keys in .env before starting the backend." -ForegroundColor Yellow
} else {
    Write-Host "  [skip] .env already exists"
}

Write-Host ""

# -- 6. Download Piper binary + voice models ---------------------------------

Write-Host "Downloading Piper and voice models..." -ForegroundColor Yellow
python scripts\download_models.py

Write-Host ""

# -- 7. Done -----------------------------------------------------------------

Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Fill in API keys in .env  (GEMINI_API_KEY, GROQ_API_KEY are required)"
Write-Host "  2. Start the backend:   .venv\Scripts\python -m uvicorn backend.main:app --reload"
Write-Host "  3. Start the frontend:  cd frontend; npm run dev"
Write-Host "  4. Hold Alt+Space to talk."
Write-Host ""
