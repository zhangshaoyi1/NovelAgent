# NovelAgent - Harness Build
# Check package installation status

param(
    [string]$ProjectDir = "..\.."
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot $ProjectDir)

Write-Host "[build] Checking package installation..." -ForegroundColor Cyan

# Check pyproject.toml
$PyProject = Join-Path $RepoRoot "pyproject.toml"
if (-not (Test-Path $PyProject)) {
    Write-Host "[build] ERROR: pyproject.toml not found" -ForegroundColor Red
    exit 1
}

# Try to import agent package
$Result = & python -c "import agent; print(f'agent v{agent.__version__}')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build] Package not installed, trying to install..." -ForegroundColor Yellow
    & pip install -e $RepoRoot 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[build] Installation failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "[build] Installation succeeded" -ForegroundColor Green
} else {
    Write-Host "[build] $Result" -ForegroundColor Green
}

Write-Host "[build] Done" -ForegroundColor Cyan