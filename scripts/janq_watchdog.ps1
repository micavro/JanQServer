param(
    [int]$Count = 50,
    [int]$StaleMinutes = 75,
    [switch]$HiddenGame = $true
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root "_runtime"
$LoopDir = Join-Path $Runtime "register_janq_loop"
$WatchdogDir = Join-Path $Runtime "watchdog"
$LogPath = Join-Path $WatchdogDir "watchdog.log"
$StatusPath = Join-Path $LoopDir "status.json"
$PrepStatusPath = Join-Path $Runtime "account_prep\status.json"
$SessionsDir = Join-Path $Runtime "sessions"
$GamePath = Join-Path $Root "sega_net_MJ\MJ\MJ.exe"
$StartScript = Join-Path $Root "start_register_janq_loop.ps1"
New-Item -ItemType Directory -Force -Path $WatchdogDir | Out-Null

function Write-WatchdogLog($Message) {
    $line = "{0} {1}" -f (Get-Date -Format o), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Read-JsonFile($Path) {
    try {
        if (Test-Path -LiteralPath $Path) {
            return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        }
    } catch {
        Write-WatchdogLog "json_read_failed path=$Path error=$($_.Exception.Message)"
    }
    return $null
}

function Get-JanQLoopProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object {
            $_.CommandLine -like "*$Root*" -and
            $_.CommandLine -like "*run_register_janq_loop.py*" -and
            ($_.CommandLine -match "(--count\s+$Count|\s-n\s+$Count)")
        }
}

function Get-JanQBotProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object {
            $_.CommandLine -like "*$Root*" -and
            $_.CommandLine -like "*janq_lab.automation.bot*"
        }
}

function Get-JanQMJProcesses {
    $resolved = $null
    try {
        $resolved = (Resolve-Path -LiteralPath $GamePath).Path
    } catch {
        return @()
    }
    Get-CimInstance Win32_Process -Filter "Name = 'MJ.exe'" |
        Where-Object {
            [string]::Equals($_.ExecutablePath, $resolved, [System.StringComparison]::OrdinalIgnoreCase)
        }
}

function Stop-JanQRuntime {
    Write-WatchdogLog "stopping_runtime"
    $targets = @()
    $targets += @(Get-JanQBotProcesses)
    $targets += @(Get-JanQLoopProcesses)
    $targets += @(Get-JanQMJProcesses)
    foreach ($proc in ($targets | Sort-Object ProcessId -Unique)) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Write-WatchdogLog "stopped pid=$($proc.ProcessId) name=$($proc.Name)"
        } catch {
            Write-WatchdogLog "stop_failed pid=$($proc.ProcessId) error=$($_.Exception.Message)"
        }
    }
    Start-Sleep -Seconds 5
}

function Start-JanQBatch {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $out = Join-Path $WatchdogDir "janq_batch_$stamp.out.log"
    $err = Join-Path $WatchdogDir "janq_batch_$stamp.err.log"
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $StartScript,
        "-Count", "$Count",
        "-HiddenGame"
    )
    Start-Process -FilePath powershell.exe -ArgumentList $args -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
    Write-WatchdogLog "started_new_batch count=$Count out=$out err=$err"
}

function Get-LatestProgressTime {
    $times = New-Object System.Collections.Generic.List[datetime]
    foreach ($path in @($StatusPath)) {
        try {
            if (Test-Path -LiteralPath $path) {
                $times.Add((Get-Item -LiteralPath $path).LastWriteTime)
            }
        } catch {}
    }
    $eventsPath = Join-Path $Runtime "logs\janq_events.jsonl"
    try {
        if (Test-Path -LiteralPath $eventsPath) {
            $semantic = Get-Content -LiteralPath $eventsPath -Tail 200 -Encoding UTF8 |
                ForEach-Object {
                    try { $_ | ConvertFrom-Json } catch { $null }
                } |
                Where-Object {
                    $_ -and
                    $_.type -ne "account_prep_screenshot_requested" -and
                    $_.type -ne "game_state_snapshot"
                } |
                Select-Object -Last 1
            if ($semantic -and $semantic.ts) {
                $times.Add([datetime]::Parse($semantic.ts).ToLocalTime())
            }
        }
    } catch {}
    try {
        Get-ChildItem -LiteralPath $SessionsDir -Filter *.jsonl -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 |
            ForEach-Object { $times.Add($_.LastWriteTime) }
    } catch {}
    try {
        Get-ChildItem -LiteralPath $LoopDir -Filter *.log -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 |
            ForEach-Object { $times.Add($_.LastWriteTime) }
    } catch {}
    if ($times.Count -eq 0) {
        return $null
    }
    return ($times | Sort-Object -Descending | Select-Object -First 1)
}

$lockPath = Join-Path $WatchdogDir "watchdog.lock"
try {
    $lock = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
    Write-WatchdogLog "another_watchdog_instance_running"
    exit 0
}

try {
    $status = Read-JsonFile $StatusPath
    $prep = Read-JsonFile $PrepStatusPath
    $loopProcs = @(Get-JanQLoopProcesses)
    $botProcs = @(Get-JanQBotProcesses)
    $mjProcs = @(Get-JanQMJProcesses)
    $latestProgress = Get-LatestProgressTime
    $progressAge = if ($latestProgress) { ((Get-Date) - $latestProgress).TotalMinutes } else { 999999 }
    $state = if ($status) { $status.state } else { "<none>" }
    $iteration = if ($status -and $status.PSObject.Properties.Name -contains "iteration") { $status.iteration } else { "<none>" }
    $completed = if ($status -and $status.PSObject.Properties.Name -contains "completed") { $status.completed } else { "<none>" }
    $failed = if ($status -and $status.PSObject.Properties.Name -contains "failed") { $status.failed } else { "<none>" }
    Write-WatchdogLog "check state=$state iteration=$iteration completed=$completed failed=$failed loopProcs=$($loopProcs.Count) botProcs=$($botProcs.Count) mjProcs=$($mjProcs.Count) progressAgeMin=$([math]::Round($progressAge,1))"

    if ($loopProcs.Count -gt 0) {
        if (
            $prep -and
            $prep.active -eq $true -and
            $prep.accountCaptured -eq $true -and
            $prep.stage -like "waiting_captured_account_login*" -and
            -not [string]::IsNullOrWhiteSpace([string]$prep.error)
        ) {
            Write-WatchdogLog "abnormal prep_login_error_stall stage=$($prep.stage) restarting"
            Stop-JanQRuntime
            Start-JanQBatch
            exit 0
        }
        if ($mjProcs.Count -eq 0) {
            Write-WatchdogLog "abnormal runner_alive_but_mj_missing restarting"
            Stop-JanQRuntime
            Start-JanQBatch
            exit 0
        }
        if ($progressAge -gt $StaleMinutes) {
            Write-WatchdogLog "abnormal stale_progress ageMin=$([math]::Round($progressAge,1)) restarting"
            Stop-JanQRuntime
            Start-JanQBatch
            exit 0
        }
        Write-WatchdogLog "normal runner_active"
        exit 0
    }

    if ($status -and $status.state -eq "complete") {
        Write-WatchdogLog "batch_complete launching_next_batch"
        Stop-JanQRuntime
        Start-JanQBatch
        exit 0
    }

    if ($status -and ($status.state -eq "failed" -or $status.state -eq "account_finished")) {
        Write-WatchdogLog "nonrunning_noncomplete_state=$($status.state) restarting_to_resume"
        Stop-JanQRuntime
        Start-JanQBatch
        exit 0
    }

    if ($prep -and $prep.active -eq $true) {
        Write-WatchdogLog "prep_active_but_runner_missing restarting_to_resume requestId=$($prep.requestId)"
        Stop-JanQRuntime
        Start-JanQBatch
        exit 0
    }

    Write-WatchdogLog "no_runner_starting_batch"
    Stop-JanQRuntime
    Start-JanQBatch
} finally {
    if ($lock) {
        $lock.Close()
    }
}
