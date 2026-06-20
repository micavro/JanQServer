param(
  [string]$SessionPath = "",
  [string]$MarkerPath = "",
  [switch]$RestartMonitor
)

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $SessionPath) {
  $latestSession = Get-ChildItem -LiteralPath (Join-Path $root "_runtime\sessions") -Filter "live_until_yakuman_*.jsonl" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($latestSession) {
    $SessionPath = $latestSession.FullName
  }
}

if (-not $MarkerPath) {
  $latestMarker = Get-ChildItem -LiteralPath (Join-Path $root "_runtime") -Filter "run_marker_*.json" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($latestMarker) {
    $MarkerPath = $latestMarker.FullName
  }
}

$procs = Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -eq "MJ.exe" -and $_.ExecutablePath -like "$root\sega_net_MJ\MJ*") -or
  ($_.Name -in @("python.exe", "pythonw.exe") -and ($_.CommandLine -match "janq_lab\.automation\.bot" -or $_.CommandLine -match "monitor_live_yakuman"))
}

$gameAlive = [bool]($procs | Where-Object { $_.Name -eq "MJ.exe" })
$botAlive = [bool]($procs | Where-Object { $_.CommandLine -match "janq_lab\.automation\.bot" })
$monitorProcs = @($procs | Where-Object { $_.CommandLine -match "monitor_live_yakuman" })
$monitorAlive = [bool]$monitorProcs

$restartedMonitor = $false
if (-not $monitorAlive -and $RestartMonitor -and $SessionPath -and $MarkerPath) {
  $marker = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $status = Join-Path $root "_runtime\captures\live_yakuman_monitor_$stamp.status.json"
  $out = Join-Path $root "_runtime\run_logs\yakuman_monitor_$stamp.out.log"
  $err = Join-Path $root "_runtime\run_logs\yakuman_monitor_$stamp.err.log"
  New-Item -ItemType Directory -Force -Path (Split-Path $status), (Split-Path $out) | Out-Null
  $monitorArgs = @(
    (Join-Path $root "scripts\monitor_live_yakuman.py"),
    "--events", (Join-Path $root "_runtime\logs\janq_events.jsonl"),
    "--session", $SessionPath,
    "--output-dir", (Join-Path $root "_runtime\captures"),
    "--start-line", ([string]$marker.startLine),
    "--status-path", $status,
    "--post-seconds", "180",
    "--post-lines", "800"
  )
  Start-Process -FilePath "python" -ArgumentList $monitorArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
  Start-Sleep -Seconds 2
  $procs = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq "MJ.exe" -and $_.ExecutablePath -like "$root\sega_net_MJ\MJ*") -or
    ($_.Name -in @("python.exe", "pythonw.exe") -and ($_.CommandLine -match "janq_lab\.automation\.bot" -or $_.CommandLine -match "monitor_live_yakuman"))
  }
  $monitorProcs = @($procs | Where-Object { $_.CommandLine -match "monitor_live_yakuman" })
  $monitorAlive = [bool]$monitorProcs
  $restartedMonitor = $monitorAlive
}

$sessionItem = if ($SessionPath -and (Test-Path -LiteralPath $SessionPath)) { Get-Item -LiteralPath $SessionPath } else { $null }
$tailObjs = @()
if ($sessionItem) {
  $tailObjs = Get-Content -LiteralPath $SessionPath -Tail 900 |
    ForEach-Object {
      try { $_ | ConvertFrom-Json } catch { $null }
    } |
    Where-Object { $null -ne $_ }
}

$latestState = $tailObjs | Where-Object { $_.type -eq "bot_state" } | Select-Object -Last 1
$latestDecision = $tailObjs | Where-Object { $_.type -eq "bot_decision" } | Select-Object -Last 1
$latestAction = $tailObjs | Where-Object { $_.type -eq "bot_action_done" } | Select-Object -Last 1

$statusFile = Get-ChildItem -LiteralPath (Join-Path $root "_runtime\captures") -Filter "live_yakuman_monitor_*.status.json" -File -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$monitorStatus = if ($statusFile) { Get-Content -LiteralPath $statusFile.FullName -Raw | ConvertFrom-Json } else { $null }

$captures = Get-ChildItem -LiteralPath (Join-Path $root "_runtime\captures") -Filter "yakuman_capture_*" -File -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 6 |
  ForEach-Object {
    [pscustomobject]@{
      name = $_.Name
      lastWrite = $_.LastWriteTime.ToString("s")
      length = $_.Length
    }
  }

$now = Get-Date
$statePayload = if ($latestState) { $latestState.payload } else { $null }
$actionPayload = if ($latestAction) { $latestAction.payload } else { $null }
$decisionPayload = if ($latestDecision) { $latestDecision.payload } else { $null }

$sessionAgeSec = if ($sessionItem) { [math]::Round(($now - $sessionItem.LastWriteTime).TotalSeconds, 1) } else { $null }
$alerts = @()
if (-not $gameAlive) { $alerts += "game_missing" }
if (-not $botAlive) { $alerts += "bot_missing" }
if (-not $monitorAlive) { $alerts += "monitor_missing" }
if ($sessionAgeSec -ne $null -and $sessionAgeSec -gt 90) { $alerts += "session_stale" }
if ($actionPayload -and -not $actionPayload.success) { $alerts += "last_action_failed" }

[pscustomobject]@{
  checkedAt = $now.ToString("s")
  gameAlive = $gameAlive
  botAlive = $botAlive
  monitorAlive = $monitorAlive
  restartedMonitor = $restartedMonitor
  alerts = $alerts
  session = if ($sessionItem) { $sessionItem.Name } else { $null }
  sessionAgeSec = $sessionAgeSec
  sessionLength = if ($sessionItem) { $sessionItem.Length } else { $null }
  monitorState = if ($monitorStatus) { $monitorStatus.state } else { $null }
  monitorLine = if ($monitorStatus) { $monitorStatus.currentLine } else { $null }
  monitorSummary = if ($monitorStatus -and $monitorStatus.summary) { $monitorStatus.summary } else { $null }
  seq = if ($latestState) { $latestState.seq } else { $null }
  phase = if ($statePayload) { $statePayload.phase } else { $null }
  mode = if ($statePayload) { $statePayload.mode } else { $null }
  status = if ($statePayload) { $statePayload.status } else { $null }
  gameState = if ($statePayload) { $statePayload.game_state } else { $null }
  mainButton = if ($statePayload) { $statePayload.main_button } else { $null }
  balls = if ($statePayload) { $statePayload.balls } else { $null }
  handIndex = if ($statePayload) { $statePayload.hand_index } else { $null }
  completedHands = if ($statePayload) { $statePayload.completed_hands } else { $null }
  mjchip = if ($statePayload) { $statePayload.currency.mjchip } else { $null }
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
  captures = $captures
} | ConvertTo-Json -Depth 8
