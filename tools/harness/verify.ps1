# NovelAgent - Harness Verify
# Unified verification: build + test + architecture compliance

param(
    [string]$ProjectDir = "..\..",
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot $ProjectDir)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  NovelAgent Harness Verification" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Build
Write-Host ">>> [1/3] Build check" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "build.ps1") -ProjectDir $ProjectDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "[verify] Build failed, stopping" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 2: Test
Write-Host ">>> [2/3] Running tests" -ForegroundColor Cyan
if ($Verbose) {
    & (Join-Path $PSScriptRoot "test.ps1") -ProjectDir $ProjectDir -Verbose
} else {
    & (Join-Path $PSScriptRoot "test.ps1") -ProjectDir $ProjectDir
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "[verify] Tests have failures" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 3: Compliance
Write-Host ">>> [3/3] Architecture compliance check" -ForegroundColor Cyan
Push-Location $RepoRoot
try {
    $Violations = 0

    # Check base/ does not depend on upper layers
    $BaseFiles = Get-ChildItem -Recurse -Filter "*.py" -Path "src/agent/base"
    $BaseDeps = Select-String -Path $BaseFiles -Pattern "(from agent\.(client|core|agents|workflows)|import agent\.(client|core|agents|workflows))"
    if ($BaseDeps) {
        Write-Host "[FAIL] base/ depends on upper layers!" -ForegroundColor Red
        $BaseDeps | ForEach-Object { Write-Host "  $($_.Path):$($_.LineNumber) $($_.Line)" -ForegroundColor Yellow }
        $Violations++
    }

    # Check client/ does not depend on core/ or above
    $ClientFiles = Get-ChildItem -Recurse -Filter "*.py" -Path "src/agent/client"
    $ClientDeps = Select-String -Path $ClientFiles -Pattern "(from agent\.(core|agents|workflows)|import agent\.(core|agents|workflows))"
    if ($ClientDeps) {
        Write-Host "[FAIL] client/ depends on core/ or above!" -ForegroundColor Red
        $ClientDeps | ForEach-Object { Write-Host "  $($_.Path):$($_.LineNumber) $($_.Line)" -ForegroundColor Yellow }
        $Violations++
    }

    if ($Violations -gt 0) {
        Write-Host "[verify] $Violations architecture violation(s) found" -ForegroundColor Red
        exit 1
    }

    Write-Host "[verify] Architecture compliance check passed" -ForegroundColor Green
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Verification all passed" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green