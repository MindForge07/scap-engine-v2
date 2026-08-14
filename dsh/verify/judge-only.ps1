# L1.5 creativity experiment - blind judge only (rerun after task outputs exist)
# Scores the 12 design outputs (A=baseline, B=assoc) for novelty + feasibility
# with a real LLM, per task (4 designs each), outputs truncated to fit the
# Windows command-line limit. Reads $outDir from the experiment run.
# Usage:
#   .\dsh\verify\judge-only.ps1 -Harness C:\path\to\deepseek-harness
param(
  [Parameter(Mandatory = $true)][string]$Harness,
  [string]$MemRoot = ""
)
$ErrorActionPreference = 'Continue'
$utf8 = [System.Text.UTF8Encoding]::new($false)
function Write-NoBom($path, $content) { [System.IO.File]::WriteAllText($path, $content, $utf8) }

if (-not $MemRoot) { $MemRoot = Join-Path $env:TEMP "scap-creativity" }
$dshHome = Join-Path $MemRoot "dsh-v"
$outDir = Join-Path $MemRoot "out"
$realProfiles = Join-Path $env:USERPROFILE ".dsh\profiles"

$tasks = @(
  "You are a systems architect. Design the synchronization architecture for a distributed knowledge base where notes are edited on many devices and must converge. Give a concrete design with components, data flow, and trade-offs.",
  "You are a systems architect. Design the rate-limiting scheme for a public REST API that must protect a shared backend during traffic spikes. Give a concrete design with components, data flow, and trade-offs.",
  "You are a systems architect. Design a resilient payment retry pipeline that must avoid duplicate charges. Give a concrete design with components, data flow, and trade-offs."
)

try {
  # Minimal isolated DSH_HOME (judge needs no SCAP plugin - pure LLM review)
  if (-not (Test-Path "$dshHome\profiles\headless")) {
    New-Item -ItemType Directory -Path "$dshHome\profiles\headless" -Force | Out-Null
    Copy-Item (Join-Path $env:USERPROFILE ".dsh\settings.yaml") "$dshHome\settings.yaml"
    Copy-Item (Join-Path $env:USERPROFILE ".dsh\.credentials.yaml") "$dshHome\.credentials.yaml"
    New-Item -ItemType Junction -Path "$dshHome\profiles\node_modules" -Target $realProfiles | Out-Null
    Write-NoBom "$dshHome\profiles\headless\package.json" @'
{
  "name": "dsh-profile-headless-test",
  "private": true,
  "dependencies": {},
  "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"] } }
}
'@
    Write-NoBom "$dshHome\profiles\headless\cordis.yml" "[]"
  }

  $mapLines = @()
  for ($t = 0; $t -lt $tasks.Count; $t++) {
    $judgePrompt = @"
You are a design review judge. Below are 4 architecture designs for the SAME
task. Score each design on:
- novelty (1-5): how fresh/unusual the approach is vs textbook solutions
- feasibility (1-5): how concrete, coherent, and implementable it is
Respond with a compact table: Dn | novelty | feasibility | one-line rationale.
Task: $($tasks[$t])
"@
    $labels = @()
    $i = 1
    foreach ($g in @('A', 'B')) {
      foreach ($r in @(1, 2)) {
        $id = "t$($t+1)-$g$r"
        $f = Join-Path $outDir "$id.txt"
        $body = Get-Content $f -Raw
        if ($body.Length -gt 3000) { $body = $body.Substring(0, 3000) }
        $judgePrompt += "`n`nD$i (design $id):`n$body"
        $labels += "D$i=$id"
        $i++
      }
    }
    $mapLines += "task$($t+1): $($labels -join ', ')"
    $env:DSH_HOME = $dshHome
    Push-Location $Harness
    $judge = node --import tsx/esm apps/cli/src/bin.ts --profile headless $judgePrompt 2>&1
    Pop-Location
    $judge | Set-Content (Join-Path $outDir "judge-t$($t+1).txt") -Encoding utf8
    Write-Output "--- judge task$($t+1) saved ($($judgePrompt.Length) chars prompt)"
  }
  $mapLines | Set-Content (Join-Path $outDir "judge-map.txt") -Encoding utf8
  Write-Output "JUDGE DONE - see $outDir\judge-t*.txt"
} finally {
  $link = "$dshHome\profiles\node_modules"
  if (Test-Path $link) { (Get-Item $link).Delete() }
}
