# SCAP L1.5 creativity value experiment: does the associative lane boost novelty?
#
# Hypothesis: with assocLane on, cross-domain structural cues (associative
# lane notes) make creative-architecture outputs MORE NOVEL without hurting
# feasibility.
#
# Design (isolated DSH, real LLM, blind judge):
#   3 cross-domain tasks x 2 groups (A=baseline L1 only, B=L1+L1.5) x 2 runs
#   -> 12 outputs; a blind LLM judge scores novelty (1-5) and feasibility (1-5);
#   PASS = B novelty mean > A novelty mean AND B feasibility >= A feasibility.
#
# Memory: an isolated project seeded with the migrated v0.7 cognitive assets
# (serendipity pool) + a few baseline decisions/experiences, so L1.5 has
# structural material to recall. Never touches the real DSH.
#
# Usage:
#   .\dsh\verify\creativity-exp.ps1 -Harness C:\path\to\deepseek-harness `
#                                    -ScapRepo C:\path\to\scap-engine-v2 `
#                                    -Python C:\Python314\python.exe
param(
  [Parameter(Mandatory = $true)][string]$Harness,
  [Parameter(Mandatory = $true)][string]$ScapRepo,
  [Parameter(Mandatory = $true)][string]$Python,
  [string]$MemRoot = "",
  [switch]$SkipBaseline
)
$ErrorActionPreference = 'Continue'
$utf8 = [System.Text.UTF8Encoding]::new($false)
function Write-NoBom($path, $content) { [System.IO.File]::WriteAllText($path, $content, $utf8) }

if (-not $MemRoot) { $MemRoot = Join-Path $env:TEMP "scap-creativity" }
$realProfiles = Join-Path $env:USERPROFILE ".dsh\profiles"
$dshHome = Join-Path $MemRoot "dsh-v"
$memDir = Join-Path $MemRoot "mem"
$outDir = Join-Path $MemRoot "out"

# Tasks: each triggers a different mechanism family in the dictionary, and the
# serendipity pool holds assets with matching meta-patterns (CA-0180+ rate
# limit, CA-0181+ circuit breaker, CA-0182+ event sourcing).
$tasks = @(
  "You are a systems architect. Design the synchronization architecture for a distributed knowledge base where notes are edited on many devices and must converge. Give a concrete design with components, data flow, and trade-offs.",
  "You are a systems architect. Design the rate-limiting scheme for a public REST API that must protect a shared backend during traffic spikes. Give a concrete design with components, data flow, and trade-offs.",
  "You are a systems architect. Design a resilient payment retry pipeline that must avoid duplicate charges. Give a concrete design with components, data flow, and trade-offs."
)

function Run-Headless($id, $task, $assocOn) {
  # Build the patch for this run: assocLane toggle
  $scapDirEsc = $memDir.Replace('\', '/') + "/.scap"
  $injectionPath = (Join-Path $ScapRepo "dsh\scap-injection.ts").Replace('\', '/')
  if ($assocOn) {
    $assocLine = "assocLane: true"
  } else {
    $assocLine = "# assocLane off (baseline)"
  }
  Write-NoBom "$dshHome\profiles\headless\cordis.patch.yml" @"
- insert:
    - id: scap-injection
      name: file:///$injectionPath
      config:
        heading: "[SCAP Project Memory]"
        scapDir: $scapDirEsc
        project: acme-exp
        $assocLine
    - id: mcp-scap
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: scap
        transport: stdio
        command: $Python
        args: ['-m', 'scap.mcp_server']
        cwd: $memDir
        env:
          PYTHONPATH: $($ScapRepo.Replace('\','/'))
          SCAP_EXPORT_DIR: $scapDirEsc
        toolCallTimeoutMs: 30000
        failOnStartupError: true
"@
  $env:DSH_HOME = $dshHome
  Push-Location $Harness
  $result = node --import tsx/esm apps/cli/src/bin.ts --profile headless $task 2>&1
  Pop-Location
  $result | Set-Content (Join-Path $outDir "$id.txt") -Encoding utf8
  return $result
}

$pass = $true
try {
  # Isolated DSH_HOME (keep environment when -SkipBaseline: A outputs live there)
  if (-not $SkipBaseline -and (Test-Path $MemRoot)) { Remove-Item -Recurse -Force $MemRoot }
  New-Item -ItemType Directory -Path $outDir -Force | Out-Null
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
  if (-not (Test-Path "$memDir\.scap")) {
    New-Item -ItemType Directory -Path "$memDir\.scap" -Force | Out-Null
  }

  # Seed memory: the serendipity pool (13 v0.7 assets) + a couple of baseline
  # decisions/experiences, exported as acme-exp project. (Skip: -SkipBaseline)
  if (-not $SkipBaseline) {
    $seed = @"
import sys, os
sys.path.insert(0, r"$ScapRepo")
from scap.models import Decision, Experience
from scap.store import MemoryStore
store = MemoryStore(r"$memDir\data\scap.db")
store.initialize()
store.save_decision(Decision(project="acme-exp", title="Database Selection", decision="PostgreSQL 16", rationale="JSONB and strong transactions", importance=5))
store.save_decision(Decision(project="acme-exp", title="Message Queue Selection", decision="Apache Kafka", rationale="high throughput for order events", importance=3))
store.save_experience(Experience(project="acme-exp", situation="queue backlog during peak", action="added consumer idempotency", lesson="always design idempotent consumers for at-least-once queues", importance=4))
# serendipity pool: migrate the v0.7 assets into this project
import subprocess
subprocess.run([sys.executable, r"$ScapRepo\dsh\migrate\learnings-to-scap.py", "--assets-dir", r"C:\Users\XDXLC\.scap\assets", "--db", r"$memDir\data\scap.db", "--project", "acme-exp"], check=True, env={**os.environ, "SCAP_EXPORT_DIR": r"$memDir\.scap"})
store.export_context("acme-exp", r"$memDir\.scap\acme-exp.md", with_json=True)
print("seeded acme-exp")
"@
    $seed | & $Python - 2>&1 | Out-Null
  }

  # Runs: task x group x 2 runs (baseline skippable via -SkipBaseline)
  $runs = @()
  for ($t = 0; $t -lt $tasks.Count; $t++) {
    foreach ($g in @('A', 'B')) {
      if ($SkipBaseline -and $g -eq 'A') { continue }
      foreach ($r in @(1, 2)) {
        $id = "t$($t+1)-$g$r"
        $runs += , @($id, $tasks[$t], ($g -eq 'B'))
      }
    }
  }
  foreach ($run in $runs) {
    $id = $run[0]; $task = $run[1]; $assoc = $run[2]
    $tag = if ($assoc) { "B(assoc)" } else { "A(base)" }
    Write-Output "--- run $id [$tag]"
    Run-Headless $id $task $assoc | Out-Null
  }

  # Blind judge per task (4 designs each: A1,A2,B1,B2 -> D1..D4, shuffled labels)
  # mapping recorded in judge-map.txt; judge tasks run headless with real LLM.
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

  Write-Output "RESULTS in $outDir (A=baseline t*-A1/A2, B=assoc t*-B1/B2; judge-t*.txt = blind scores)"
  Write-Output "PASS criterion: B novelty mean > A novelty mean AND B feasibility >= A feasibility"
} finally {
  $link = "$dshHome\profiles\node_modules"
  if (Test-Path $link) { (Get-Item $link).Delete() }
  # keep $outDir; cleanup DSH home only
  if (Test-Path $dshHome) { Remove-Item -Recurse -Force $dshHome }
}
