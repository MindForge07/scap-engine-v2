# SCAP real-composition smoke: L1 layered injection in a real DSH (isolated)
# Usage:
#   .\dsh\verify\smoke.ps1 -Harness C:\path\to\deepseek-harness `
#                          -ScapRepo C:\path\to\scap-engine-v2 `
#                          -Python C:\Python314\python.exe
# Requires: DSH checkout with installed deps, real LLM credentials in ~/.dsh.
# Creates an isolated DSH_HOME, runs preset-write (A) then L1 verification (B),
# asserts layered-injection behavior, cleans up. Never touches the real DSH.
param(
  [Parameter(Mandatory = $true)][string]$Harness,
  [Parameter(Mandatory = $true)][string]$ScapRepo,
  [Parameter(Mandatory = $true)][string]$Python,
  [string]$MemRoot = ""
)
$ErrorActionPreference = 'Continue'
$utf8 = [System.Text.UTF8Encoding]::new($false)
function Write-NoBom($path, $content) { [System.IO.File]::WriteAllText($path, $content, $utf8) }

if (-not $MemRoot) { $MemRoot = Join-Path $env:TEMP "scap-verify" }
$realProfiles = Join-Path $env:USERPROFILE ".dsh\profiles"
$dshHome = Join-Path $MemRoot "dsh-v"
$memDir = Join-Path $MemRoot "mem"
$outDir = Join-Path $MemRoot "out"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

function Run-Headless($id, $task) {
  $env:DSH_HOME = $dshHome
  Push-Location $Harness
  $result = node --import tsx/esm apps/cli/src/bin.ts --profile headless $task 2>&1
  $code = $LASTEXITCODE
  Pop-Location
  New-Item -ItemType Directory -Path $outDir -Force | Out-Null
  $result | Set-Content (Join-Path $outDir "$id.txt") -Encoding utf8
  return $result
}

$taskA = "You are the architect of the acme-pay order system. Record project memory via the mcp__scap__ tools (project=acme-pay): 1) scap_remember: title=Database Selection, decision=PostgreSQL 16, rationale=JSONB and strong transactions, importance=5; 2) scap_remember: title=Message Queue Selection, decision=Apache Kafka, rationale=high throughput for order events, importance=3; 3) scap_remember: title=Cache Solution, decision=Redis, rationale=low latency hot paths, importance=3; 4) scap_remember: title=Frontend Framework Choice, decision=React, rationale=ecosystem, importance=2; 5) scap_record_experience: situation=message queue backlog during peak, action=added consumer idempotency and partition scale-out, lesson=always design idempotent consumers for Kafka at-least-once, importance=4; 6) scap_reflect: insights=[order events must be replayable for audit]. Report each tool result briefly."
$taskB = "You are the architect of the acme-pay order system. The order system needs a message queue solution for a 50k msg/s peak. Step 1: output VERBATIM the complete [SCAP Project Memory] section from your system prompt. Step 2: call mcp__scap__scap_remember (project=acme-pay, title=Message Queue Upgrade Plan, decision=Kafka with more partitions and schema registry, rationale=cope with 50k msg/s peak, importance=4). Step 3: output VERBATIM again the complete [SCAP Project Memory] section from your system prompt. Finally list which decision titles were present in step 1 and step 3."

$scapDirEsc = $memDir.Replace('\', '/') + "/.scap"
$injectionPath = (Join-Path $ScapRepo "dsh\scap-injection.ts").Replace('\', '/')
$harnessNode = Join-Path $Harness "node_modules"

try {
  # Isolated DSH_HOME
  if (Test-Path $MemRoot) { Remove-Item -Recurse -Force $MemRoot }
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
  New-Item -ItemType Directory -Path "$memDir\.scap" -Force | Out-Null
  Write-NoBom "$dshHome\profiles\headless\cordis.patch.yml" @"
- insert:
    - id: scap-injection
      name: file:///$injectionPath
      config:
        heading: "[SCAP Project Memory]"
        scapDir: $scapDirEsc
        project: acme-pay
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

  # A: preset write
  $outA = Run-Headless "A" $taskA
  Write-Output "--- A (preset write) exit: $LASTEXITCODE"

  # Stalen Frontend + re-export so L1 filtering is testable
  $pythonCode = @"
import sqlite3, sys
sys.path.insert(0, r"$ScapRepo")
from datetime import datetime, timedelta, timezone
import scap.store as s
db = sqlite3.connect(r"$memDir\data\scap.db")
old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
cur = db.execute("UPDATE decisions SET updated_at=? WHERE title='Frontend Framework Choice'", (old,))
db.commit(); db.close()
store = s.MemoryStore(r"$memDir\data\scap.db")
store.initialize()
store.export_context("acme-pay", r"$memDir\.scap\acme-pay.md")
print("staled:", cur.rowcount)
"@
  $pythonCode | & $Python - 2>&1 | Out-Null

  # B: L1 verification
  $outB = Run-Headless "B" $taskB
  $bText = ($outB | Out-String)

  # Assertions (ASCII only: PowerShell 5.1 reads BOM-less scripts as GBK and
  # CJK literals corrupt parsing - see CONTRIBUTING environment pitfalls).
  $pass = $true
  if ($bText -notmatch "Message Queue Selection")   { $pass = $false; Write-Output "FAIL: task-relevant decision not injected" }
  if ($bText -notmatch "Message Queue Upgrade Plan") { $pass = $false; Write-Output "FAIL: post-write injection did not update" }
  if ($bText -match "Frontend Framework Choice")     { $pass = $false; Write-Output "FAIL: stale irrelevant decision injected" }

  if ($pass) { Write-Output "SMOKE PASS" } else { Write-Output "SMOKE FAIL - see $outDir\B.txt" }
} finally {
  $link = "$dshHome\profiles\node_modules"
  if (Test-Path $link) { (Get-Item $link).Delete() }
  if (Test-Path $MemRoot) { Remove-Item -Recurse -Force $MemRoot }
}
