# Focused-analogy experiment - re-judge on full design sections only
# The first judge pass truncated at 2600 chars, which penalized groups B/C
# (longer Step 1 reasoning pushed their Step 2 design past the cutoff).
# This pass extracts everything after "Step 2" per output and re-scores.
# Usage:
#   .\dsh\verify\analogy-judge.ps1 -Harness C:\path\to\deepseek-harness
param(
  [Parameter(Mandatory = $true)][string]$Harness,
  [string]$MemRoot = ""
)
$ErrorActionPreference = 'Continue'
$utf8 = [System.Text.UTF8Encoding]::new($false)
function Write-NoBom($path, $content) { [System.IO.File]::WriteAllText($path, $content, $utf8) }

if (-not $MemRoot) { $MemRoot = Join-Path $env:TEMP "scap-analogy" }
$dshHome = Join-Path $MemRoot "dsh-v"
$outDir = Join-Path $MemRoot "out"
$realProfiles = Join-Path $env:USERPROFILE ".dsh\profiles"

$task = @(
  "You are a systems architect. Design the synchronization architecture for a distributed knowledge base where notes are edited on many devices and must converge. Step 1: describe how you approach this problem. Step 2: give a concrete design with components, data flow, and trade-offs.",
  "You are a systems architect. Design the rate-limiting scheme for a public REST API that must protect a shared backend during traffic spikes. Step 1: describe how you approach this problem. Step 2: give a concrete design with components, data flow, and trade-offs.",
  "You are a systems architect. Design a resilient payment retry pipeline that must avoid duplicate charges. Step 1: describe how you approach this problem. Step 2: give a concrete design with components, data flow, and trade-offs."
)

function Extract-Step2($path) {
  $text = Get-Content $path -Raw
  $idx = $text.IndexOf("Step 2")
  if ($idx -lt 0) { return $text }
  return $text.Substring($idx)
}

try {
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

  for ($t = 0; $t -lt 3; $t++) {
    $judgePrompt = @"
You are a design review judge. Below are the DESIGN SECTIONS (Step 2) of 6
architectures for the SAME task. Score each on:
- quality (1-5): concreteness, coherence, implementability
- depth (1-5): how deeply it handles the problem's hard parts
Also flag (yes/no) reuse of strategy elements:
E1 <append-only log / replay / convergence by replay>
E2 <measure input, explicit cap, shed lowest priority first, act before the cap>
E3 <unique id at first acceptance, check-before-write, duplicate returns original outcome>
Respond with a compact table: Dn | quality | depth | E1 | E2 | E3 | one-line rationale.
Task: $($task[$t])
"@
    $labels = @()
    $i = 1
    foreach ($g in @('A', 'B', 'C')) {
      foreach ($r in @(1, 2)) {
        $id = "t$($t+1)-$g$r"
        $body = Extract-Step2 (Join-Path $outDir "$id.txt")
        if ($body.Length -gt 3200) { $body = $body.Substring(0, 3200) }
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
    $judge | Set-Content (Join-Path $outDir "judge2-t$($t+1).txt") -Encoding utf8
    Write-Output "--- judge2 task$($t+1) saved"
  }
  $mapLines | Set-Content (Join-Path $outDir "judge2-map.txt") -Encoding utf8
  Write-Output "JUDGE2 DONE - see judge2-t*.txt"
} finally {
  $link = "$dshHome\profiles\node_modules"
  if (Test-Path $link) { (Get-Item $link).Delete() }
  if (Test-Path $dshHome) { Remove-Item -Recurse -Force $dshHome }
}
