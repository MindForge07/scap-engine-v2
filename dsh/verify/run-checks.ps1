# Full verification gate: unit + golden + coverage (fail_under from pyproject).
# Usage: .\dsh\verify\run-checks.ps1 [-Python C:\Python314\python.exe]
# This is THE gate: CI runs the same command. Single-file debugging can use
# `pytest tests/test_x.py` freely — the gate only fires here and in CI.
param([string]$Python = "python")

# Anchor to the repository root regardless of the caller's cwd.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

Write-Output "=== SCAP full verification gate ==="
& $Python -m pytest tests/ --cov=scap --cov-report=term-missing -q
exit $LASTEXITCODE
