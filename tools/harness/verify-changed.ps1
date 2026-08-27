# NovelAgent - Harness Verify-Changed
# Incremental test selection based on git diff

param(
    [string]$ProjectDir = "..\..",
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot $ProjectDir)

Push-Location $RepoRoot
try {
    Write-Host "[verify-changed] Detecting changed files..." -ForegroundColor Cyan

    $ChangedFiles = & git diff --name-only HEAD 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[verify-changed] Not a git repository, running full test suite" -ForegroundColor Yellow
        & python -m pytest -v --tb=short
        exit $LASTEXITCODE
    }

    if (-not $ChangedFiles) {
        Write-Host "[verify-changed] No changes detected" -ForegroundColor Green
        exit 0
    }

    Write-Host "[verify-changed] Changed files:" -ForegroundColor Cyan
    $ChangedFiles | ForEach-Object { Write-Host "  $_" }

    # Select test paths based on changed files
    $TestPaths = @()
    $ChangedFiles | ForEach-Object {
        if ($_ -match "^src/agent/agents/") {
            $TestPaths += "tests/phase2/"
        }
        if ($_ -match "^src/agent/core/") {
            $TestPaths += "tests/test_*.py"
        }
        if ($_ -match "^src/agent/workflows/") {
            $TestPaths += "tests/phase3/"
        }
        if ($_ -match "^src/agent/cli/") {
            $TestPaths += "tests/phase4/"
        }
        if ($_ -match "^tests/") {
            $TestPaths += $_
        }
    }
    $TestPaths = $TestPaths | Select-Object -Unique

    if ($TestPaths.Count -eq 0) {
        Write-Host "[verify-changed] No relevant tests detected for changes" -ForegroundColor Yellow
        Write-Host "[verify-changed] Suggestion: run full test suite to confirm no regression" -ForegroundColor Yellow
        exit 0
    }

    Write-Host "[verify-changed] Running relevant tests:" -ForegroundColor Cyan
    $TestPaths | ForEach-Object { Write-Host "  $_" }

    $PytestArgs = @()
    if ($Verbose) { $PytestArgs += "-v" }
    $PytestArgs += "--tb=short"
    $PytestArgs += $TestPaths

    $Result = & python -m pytest @PytestArgs 2>&1
    $ExitCode = $LASTEXITCODE

    if ($ExitCode -eq 0) {
        Write-Host "[verify-changed] Passed" -ForegroundColor Green
    } else {
        Write-Host "[verify-changed] Failed" -ForegroundColor Red
    }

    exit $ExitCode
} finally {
    Pop-Location
}