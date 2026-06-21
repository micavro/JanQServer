param(
  [string]$SessionPath = "",
  [int]$TargetMjchip = 4000,
  [int]$BankruptcyMjchip = 9
)

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $SessionPath) {
  $latestSession = Get-ChildItem -LiteralPath (Join-Path $root "_runtime\sessions") -Filter "live_to_4000_or_bust_*.jsonl" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($latestSession) {
    $SessionPath = $latestSession.FullName
  }
}

$procs = Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -eq "MJ.exe" -and $_.ExecutablePath -like "$root\sega_net_MJ\MJ*") -or
  ($_.Name -in @("python.exe", "pythonw.exe") -and $_.CommandLine -match "janq_lab\.automation\.bot")
}
$gameAlive = [bool]($procs | Where-Object { $_.Name -eq "MJ.exe" })
$botAlive = [bool]($procs | Where-Object { $_.CommandLine -match "janq_lab\.automation\.bot" })

$sessionItem = if ($SessionPath -and (Test-Path -LiteralPath $SessionPath)) { Get-Item -LiteralPath $SessionPath } else { $null }
$tailObjs = @()
if ($sessionItem) {
  $tailObjs = Get-Content -LiteralPath $SessionPath -Tail 1500 |
    ForEach-Object {
      try { $_ | ConvertFrom-Json } catch { $null }
    } |
    Where-Object { $null -ne $_ }
}

$latestState = $tailObjs | Where-Object { $_.type -eq "bot_state" } | Select-Object -Last 1
$latestDecision = $tailObjs | Where-Object { $_.type -eq "bot_decision" } | Select-Object -Last 1
$latestAction = $tailObjs | Where-Object { $_.type -eq "bot_action_done" } | Select-Object -Last 1
$latestPause = $tailObjs | Where-Object { $_.type -eq "bot_pause" } | Select-Object -Last 1
$latestSummary = $tailObjs | Where-Object { $_.type -eq "bot_session_summary" } | Select-Object -Last 1

$statePayload = if ($latestState) { $latestState.payload } else { $null }
$actionPayload = if ($latestAction) { $latestAction.payload } else { $null }
$decisionPayload = if ($latestDecision) { $latestDecision.payload } else { $null }
$pausePayload = if ($latestPause) { $latestPause.payload } else { $null }
$summaryPayload = if ($latestSummary) { $latestSummary.payload } else { $null }

$now = Get-Date
$sessionAgeSec = if ($sessionItem) { [math]::Round(($now - $sessionItem.LastWriteTime).TotalSeconds, 1) } else { $null }
$mjchip = if ($statePayload) { $statePayload.currency.mjchip } else { $null }
$startMjchip = if ($statePayload) { $statePayload.currency.start_mjchip } else { $null }
$deltaMjchip = if ($mjchip -ne $null -and $startMjchip -ne $null) { $mjchip - $startMjchip } else { $null }

$terminalReason = $null
if ($pausePayload -and $pausePayload.reason) {
  $terminalReason = $pausePayload.reason
} elseif ($mjchip -ne $null -and $mjchip -ge $TargetMjchip) {
  $terminalReason = "target_mjchip_observed"
} elseif ($mjchip -ne $null -and $mjchip -le $BankruptcyMjchip) {
  $terminalReason = "bankruptcy_mjchip_observed"
}

$alerts = @()
if (-not $gameAlive) { $alerts += "game_missing" }
if (-not $botAlive -and -not $terminalReason) { $alerts += "bot_missing_without_terminal_reason" }
if ($sessionAgeSec -ne $null -and $sessionAgeSec -gt 90 -and -not $terminalReason) { $alerts += "session_stale" }
if ($actionPayload -and -not $actionPayload.success) { $alerts += "last_action_failed" }

[pscustomobject]@{
  checkedAt = $now.ToString("s")
  gameAlive = $gameAlive
  botAlive = $botAlive
  alerts = $alerts
  terminal = [bool]$terminalReason
  terminalReason = $terminalReason
  targetMjchip = $TargetMjchip
  bankruptcyMjchip = $BankruptcyMjchip
  session = if ($sessionItem) { $sessionItem.Name } else { $null }
  sessionAgeSec = $sessionAgeSec
  sessionLength = if ($sessionItem) { $sessionItem.Length } else { $null }
  seq = if ($latestState) { $latestState.seq } else { $null }
  phase = if ($statePayload) { $statePayload.phase } else { $null }
  mode = if ($statePayload) { $statePayload.mode } else { $null }
  status = if ($statePayload) { $statePayload.status } else { $null }
  gameState = if ($statePayload) { $statePayload.game_state } else { $null }
  mainButton = if ($statePayload) { $statePayload.main_button } else { $null }
  balls = if ($statePayload) { $statePayload.balls } else { $null }
  handIndex = if ($statePayload) { $statePayload.hand_index } else { $null }
  completedHands = if ($statePayload) { $statePayload.completed_hands } else { $null }
  mjchip = $mjchip
  startMjchip = $startMjchip
  deltaMjchip = $deltaMjchip
  cchip = if ($statePayload) { $statePayload.currency.cchip } else { $null }
  isReach = if ($statePayload) { $statePayload.is_reach } else { $null }
  lastResult = if ($statePayload) { $statePayload.last_result } else { $null }
  latestDecision = if ($decisionPayload) {
    [pscustomobject]@{
      seq = $latestDecision.seq
      action = $decisionPayload.action
      reason = $decisionPayload.reason
    }
  } else { $null }
  latestAction = if ($actionPayload) {
    [pscustomobject]@{
      seq = $latestAction.seq
      success = $actionPayload.success
      action = $actionPayload.action
      betRate = $actionPayload.details.bridge_result.state.betRate
      bridgeError = $actionPayload.details.bridge_result.error
    }
  } else { $null }
  latestPause = if ($pausePayload) {
    [pscustomobject]@{
      seq = $latestPause.seq
      reason = $pausePayload.reason
      mjchip = if ($pausePayload.mjchip) { $pausePayload.mjchip } else { $null }
    }
  } else { $null }
  hasSessionSummary = [bool]$summaryPayload
} | ConvertTo-Json -Depth 8
