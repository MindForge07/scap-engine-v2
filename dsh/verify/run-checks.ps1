# Full verification gate: unit + golden + coverage (fail_under from pyproject).
# Usage: .\dsh\verify\run-checks.ps1 [-Python C:\Python314\python.exe]
# Optional: L1.5 associative-lane logic checks (TS, needs a DSH checkout):
#   $env:DSH_HARNESS = "C:\path\to\deepseek-harness"; .\dsh\verify\run-checks.ps1
# This is THE gate: CI runs the same command. Single-file debugging can use
# `pytest tests/test_x.py` freely — the gate only fires here and in CI.
param([string]$Python = "python")

# Anchor to the repository root regardless of the caller's cwd.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

Write-Output "=== SCAP full verification gate ==="
& $Python -m pytest tests/ --cov=scap --cov-report=term-missing -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($env:DSH_HARNESS) {
  Write-Output "=== L1.5 associative-lane logic checks (DSH tsx) ==="
  Push-Location $env:DSH_HARNESS
  node --import tsx/esm (Join-Path $RepoRoot "dsh\verify\l15-check.ts")
  $l15 = $LASTEXITCODE
  Pop-Location
  if ($l15 -ne 0) { exit $l15 }
}
exit 0
