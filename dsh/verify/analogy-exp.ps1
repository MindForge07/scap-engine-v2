# SCAP focused-analogy experiment: does "same-essence past experience"
# focused thinking beat directly pondering a complex problem?
#
# Theory: Gick & Holyoak (1980) radiation problem - analogical transfer works
# when the source shares deep structure with the target, and EXPLICITLY telling
# the solver the story is relevant jumps transfer from ~30% to ~80%.
#
# Design (isolated DSH, real LLM, blind judge):
#   Group A: think directly about the task (no injection) - baseline
#   Group B: same-essence past experience injected + focus instruction
#            ("fundamentally the same problem, analyze how it was solved,
#             then transfer the strategy")
#   Group C: same-essence past experience injected, NO focus instruction
#            (controls for the "hint" effect from the radiation experiment)
#   3 complex tasks x 3 groups x 2 runs = 18 outputs; blind LLM judge scores
#   quality (1-5), depth (1-5), and flags transferred strategy elements.
#   PASS = B quality/depth > A AND B > C (analogy gain + hint effect).
#
# Essence cases are CONSTRUCTED for the isolated experiment only - they are
# NOT written into any production SCAP memory (memory-correctness gate).
#
# Usage:
#   .\dsh\verify\analogy-exp.ps1 -Harness C:\path\to\deepseek-harness
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

# --- Tasks (complex, open-ended) ---
$task = @(
  "You are a systems architect. Design the synchronization architecture for a distributed knowledge base where notes are edited on many devices and must converge. Step 1: describe how you approach this problem. Step 2: give a concrete design with components, data flow, and trade-offs.",
  "You are a systems architect. Design the rate-limiting scheme for a public REST API that must protect a shared backend during traffic spikes. Step 1: describe how you approach this problem. Step 2: give a concrete design with components, data flow, and trade-offs.",
  "You are a systems architect. Design a resilient payment retry pipeline that must avoid duplicate charges. Step 1: describe how you approach this problem. Step 2: give a concrete design with components, data flow, and trade-offs."
)

# --- Essence cases (constructed for the isolated experiment only) ---
# Same deep structure as the task, different domain. situation/action/lesson
# shape mirrors SCAP experiences so results are transferable to real memory.
$case = @(
  "A military logistics team maintains copies of a battlefield map on three trucks. Each truck edits its copy during the mission, they reconnect irregularly, and the maps must converge without losing any update. Situation: three diverging map copies with no central store. Action: every edit is logged as an entry with a unique id and truck origin; periodic merge sessions replay all entries in order and resolve conflicts newest-entry-wins, keeping a log of what was discarded. Lesson: treat every copy as an append-only log of intents, never overwrite, make the merge replayable, and converge by replay not by reconciliation of states.",
  "A hydroelectric dam controls water release to protect a downstream river town. Upstream rainfall is bursty and unpredictable. Situation: bursty upstream inflow, fixed downstream channel capacity. Action: release schedule computed from rolling inflow measurement; hard cap on instantaneous release; warning threshold that pre-reduces flow before the cap is hit; lowest-priority releases shed first. Lesson: measure the input rate, set an explicit cap below physical limits, shed lowest-priority load first, act before the cap is reached rather than after.",
  "A bank branch handles customer deposit slips. Customers often submit the same slip twice because they are unsure the first submission was received. Situation: duplicate submissions of the same deposit. Action: every slip gets a unique receipt number at first acceptance; teller checks the receipt number against the ledger before posting; duplicates are acknowledged with the original posting result instead of posting again. Lesson: issue a unique id at first acceptance, check-before-write on the id, and make the duplicate path return the original outcome."
)

function Run-Headless($id, $prompt) {
  $env:DSH_HOME = $dshHome
  Push-Location $Harness
  $result = node --import tsx/esm apps/cli/src/bin.ts --profile headless $prompt 2>&1
  Pop-Location
  $result | Set-Content (Join-Path $outDir "$id.txt") -Encoding utf8
  return $result
}

$focus = "Before designing, study this past experience. It is fundamentally the same problem as yours, solved in a different domain. Analyze HOW it was solved, then transfer its strategy to your design."

try {
  # Isolated DSH_HOME (no SCAP plugin - controlled prompt injection only)
  if (Test-Path $MemRoot) { Remove-Item -Recurse -Force $MemRoot }
  New-Item -ItemType Directory -Path $outDir -Force | Out-Null
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

  # 18 runs: task x group(A/B/C) x 2
  $runs = @()
  for ($t = 0; $t -lt 3; $t++) {
    foreach ($g in @('A', 'B', 'C')) {
      foreach ($r in @(1, 2)) {
        $id = "t$($t+1)-$g$r"
        if ($g -eq 'A') {
          $prompt = $task[$t]
        } elseif ($g -eq 'B') {
          $prompt = "$($task[$t])`n`n$focus`n`n[Past experience]$($case[$t])"
        } else {
          $prompt = "$($task[$t])`n`n[Past experience from another project]$($case[$t])"
        }
        $runs += , @($id, $prompt)
      }
    }
  }
  foreach ($run in $runs) {
    $id = $run[0]; $prompt = $run[1]
    Write-Output "--- run $id ($($prompt.Length) chars)"
    Run-Headless $id $prompt | Out-Null
  }

  # Blind judge per task (6 designs: A1,A2,B1,B2,C1,C2 -> D1..D6)
  $mapLines = @()
  for ($t = 0; $t -lt 3; $t++) {
    $judgePrompt = @"
You are a design review judge. Below are 6 architecture designs for the SAME
task. For each design score:
- quality (1-5): concreteness, coherence, implementability
- depth (1-5): how deeply the reasoning covers the problem's hard parts
Also flag (yes/no) whether the design reuses these strategy elements:
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
        $body = Get-Content (Join-Path $outDir "$id.txt") -Raw
        if ($body.Length -gt 2600) { $body = $body.Substring(0, 2600) }
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
    Write-Output "--- judge task$($t+1) saved"
  }
  $mapLines | Set-Content (Join-Path $outDir "judge-map.txt") -Encoding utf8
  Write-Output "ANALOGY EXP DONE - results in $outDir"
  Write-Output "PASS = B quality/depth > A AND B > C (analogy gain + hint effect)"
} finally {
  $link = "$dshHome\profiles\node_modules"
  if (Test-Path $link) { (Get-Item $link).Delete() }
  if (Test-Path $dshHome) { Remove-Item -Recurse -Force $dshHome }
}
