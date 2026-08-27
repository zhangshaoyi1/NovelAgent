# NovelAgent - Harness Test
# Run full test suite

param(
    [string]$ProjectDir = "..\..",
    [string]$Filter = "",
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot $ProjectDir)

Write-Host "[test] Running tests..." -ForegroundColor Cyan

$PytestCmd = "python -m pytest"
if ($Verbose) {
    $PytestCmd += " -v"
}
if ($Filter) {
    $PytestCmd += " -k `"$Filter`""
}
$PytestCmd += " --tb=short"

Write-Host "[test] $PytestCmd" -ForegroundColor Gray
$Result = Invoke-Expression $PytestCmd 2>&1
$ExitCode = $LASTEXITCODE

if ($ExitCode -eq 0) {
    Write-Host "[test] All passed" -ForegroundColor Green
} else {
    Write-Host "[test] Some tests failed" -ForegroundColor Red
}

exit $ExitCode