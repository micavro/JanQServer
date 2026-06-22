param(
    [int]$Count = 50,
    [int]$Bet = 50,
    [int]$TargetMjchip = 20000,
    [int]$BankruptcyMjchip = 49,
    [int]$GameWidth = 320,
    [int]$GameHeight = 180,
    [string]$NicknamePrefix = "JanQ",
    [int]$PrepTimeoutSeconds = 7200,
    [int]$PrepLoadingStallSeconds = 150,
    [int]$PrepGenericStallSeconds = 420,
    [int]$PrepMaxStories = 0,
    [int]$BotMaxHands = 1000000,
    [int]$BotMaxRuntimeSeconds = 8640000,
    [int]$ExitTimeoutSeconds = 25,
    [int]$MaxAccountResumeFailures = 5,
    [int]$MaxPrepRestartsPerAccount = 5,
    [ValidateSet("public", "greedy", "route_ev", "route_ev2")]
    [string]$Strategy = "route_ev",
    [int]$StaleMinutes = 75,
    [switch]$ShowGame,
    [switch]$HiddenGame = $true,
    [switch]$FreshGame,
    [switch]$FreshPrep,
    [switch]$NoResumeStopped,
    [switch]$ContinueOnError
)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$Runtime = Join-Path $Root "_runtime"
$LoopDir = Join-Path $Runtime "register_janq_loop"
$WatchdogDir = Join-Path $Runtime "watchdog"
$LogPath = Join-Path $WatchdogDir "watchdog.log"
$StatusPath = Join-Path $LoopDir "status.json"
$HealthPath = Join-Path $LoopDir "health.json"
$LaunchArgsPath = Join-Path $LoopDir "launch_args.json"
$PrepStatusPath = Join-Path $Runtime "account_prep\status.json"
$SessionsDir = Join-Path $Runtime "sessions"
$GamePath = Join-Path $Root "sega_net_MJ\MJ\MJ.exe"
$StartScript = Join-Path $Root "start_register_janq_loop.ps1"
New-Item -ItemType Directory -Force -Path $WatchdogDir | Out-Null

function Write-WatchdogLog($Message) {
    $line = "{0} {1}" -f (Get-Date -Format o), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Write-JsonAtomic($Path, $Value) {
    try {
        $dir = Split-Path -Parent $Path
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $tmp = Join-Path $dir (".{0}.tmp" -f ([guid]::NewGuid().ToString("N")))
        $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $tmp -Encoding UTF8
        Move-Item -LiteralPath $tmp -Destination $Path -Force
    } catch {
        Write-WatchdogLog "json_write_failed path=$Path error=$($_.Exception.Message)"
    }
}

function Write-WatchdogHealth($Status, $Prep, $LoopProcs, $BotProcs, $MjProcs, $ProgressAge, $Alert, $Action) {
    $payload = [ordered]@{
        checkedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        source = "watchdog"
        state = if ($Status) { $Status.state } else { $null }
        attempt = if ($Status -and $Status.PSObject.Properties.Name -contains "attempt") { $Status.attempt } else { $null }
        iteration = if ($Status -and $Status.PSObject.Properties.Name -contains "iteration") { $Status.iteration } else { $null }
        count = $Count
        completed = if ($Status -and $Status.PSObject.Properties.Name -contains "completed") { $Status.completed } else { $null }
        failed = if ($Status -and $Status.PSObject.Properties.Name -contains "failed") { $Status.failed } else { $null }
        requestId = if ($Status -and $Status.PSObject.Properties.Name -contains "requestId") { $Status.requestId } elseif ($Prep) { $Prep.requestId } else { $null }
        nickname = if ($Status -and $Status.PSObject.Properties.Name -contains "nickname") { $Status.nickname } elseif ($Prep) { $Prep.nickname } else { $null }
        prep = if ($Prep) {
            [ordered]@{
                active = $Prep.active
                stage = $Prep.stage
                requestId = $Prep.requestId
                accountCaptured = $Prep.accountCaptured
                error = $Prep.error
            }
        } else { $null }
        processes = [ordered]@{
            loop = @($LoopProcs).Count
            bot = @($BotProcs).Count
            mj = @($MjProcs).Count
        }
        progressAgeMin = if ($ProgressAge -ne $null) { [math]::Round($ProgressAge, 1) } else { $null }
        alert = $Alert
        lastRecoveryAction = $Action
    }
    Write-JsonAtomic $HealthPath $payload
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

function Apply-SavedLaunchArgs {
    $saved = Read-JsonFile $LaunchArgsPath
    if (-not $saved) {
        return
    }
    foreach ($name in @(
        "Count", "Bet", "TargetMjchip", "BankruptcyMjchip", "GameWidth", "GameHeight",
        "NicknamePrefix", "PrepTimeoutSeconds", "PrepLoadingStallSeconds", "PrepGenericStallSeconds",
        "PrepMaxStories", "BotMaxHands", "BotMaxRuntimeSeconds", "ExitTimeoutSeconds",
        "MaxAccountResumeFailures", "MaxPrepRestartsPerAccount", "Strategy"
    )) {
        if ($saved.PSObject.Properties.Name -contains $name) {
            Set-Variable -Scope Script -Name $name -Value $saved.$name
        }
    }
    foreach ($name in @("ShowGame", "HiddenGame", "FreshGame", "FreshPrep", "NoResumeStopped", "ContinueOnError")) {
        if ($saved.PSObject.Properties.Name -contains $name) {
            Set-Variable -Scope Script -Name $name -Value ([bool]$saved.$name)
        }
    }
}

Apply-SavedLaunchArgs

function Test-CommandLineInWorkspace($CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    $workspacePattern = [regex]::Escape($Root.TrimEnd('\')) + '(\\|"|\s|$)'
    return $CommandLine -match $workspacePattern
}

function Get-JanQLoopProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object {
            (Test-CommandLineInWorkspace $_.CommandLine) -and
            $_.CommandLine -like "*run_register_janq_loop.py*" -and
            ($_.CommandLine -match "(--count\s+$Count|\s-n\s+$Count)")
        }
}

function Get-JanQBotProcesses {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object {
            (Test-CommandLineInWorkspace $_.CommandLine) -and
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
        "-Bet", "$Bet",
        "-TargetMjchip", "$TargetMjchip",
        "-BankruptcyMjchip", "$BankruptcyMjchip",
        "-GameWidth", "$GameWidth",
        "-GameHeight", "$GameHeight",
        "-NicknamePrefix", "$NicknamePrefix",
        "-PrepTimeoutSeconds", "$PrepTimeoutSeconds",
        "-PrepLoadingStallSeconds", "$PrepLoadingStallSeconds",
        "-PrepGenericStallSeconds", "$PrepGenericStallSeconds",
        "-BotMaxHands", "$BotMaxHands",
        "-BotMaxRuntimeSeconds", "$BotMaxRuntimeSeconds",
        "-ExitTimeoutSeconds", "$ExitTimeoutSeconds",
        "-MaxAccountResumeFailures", "$MaxAccountResumeFailures",
        "-MaxPrepRestartsPerAccount", "$MaxPrepRestartsPerAccount",
        "-Strategy", "$Strategy"
    )
    if ($PrepMaxStories -gt 0) { $args += @("-PrepMaxStories", "$PrepMaxStories") }
    if ($ShowGame) { $args += "-ShowGame" }
    if ($HiddenGame) { $args += "-HiddenGame" }
    if ($FreshGame) { $args += "-FreshGame" }
    if ($FreshPrep) { $args += "-FreshPrep" }
    if ($NoResumeStopped) { $args += "-NoResumeStopped" }
    if ($ContinueOnError) { $args += "-ContinueOnError" }
    Start-Process -FilePath powershell.exe -ArgumentList $args -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null
    Write-WatchdogLog "started_new_batch count=$Count bet=$Bet target=$TargetMjchip bankruptcy=$BankruptcyMjchip strategy=$Strategy out=$out err=$err"
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
            Write-WatchdogHealth $status $prep $loopProcs $botProcs $mjProcs $progressAge "prep_login_error_stall" "restart_batch"
            Stop-JanQRuntime
            Start-JanQBatch
            exit 0
        }
        if ($mjProcs.Count -eq 0) {
            Write-WatchdogLog "abnormal runner_alive_but_mj_missing restarting"
            Write-WatchdogHealth $status $prep $loopProcs $botProcs $mjProcs $progressAge "runner_alive_but_mj_missing" "restart_batch"
            Stop-JanQRuntime
            Start-JanQBatch
            exit 0
        }
        if ($progressAge -gt $StaleMinutes) {
            Write-WatchdogLog "abnormal stale_progress ageMin=$([math]::Round($progressAge,1)) restarting"
            Write-WatchdogHealth $status $prep $loopProcs $botProcs $mjProcs $progressAge "stale_progress" "restart_batch"
            Stop-JanQRuntime
            Start-JanQBatch
            exit 0
        }
        Write-WatchdogLog "normal runner_active"
        Write-WatchdogHealth $status $prep $loopProcs $botProcs $mjProcs $progressAge $null "none"
        exit 0
    }

    if ($status -and $status.state -eq "complete") {
        Write-WatchdogLog "batch_complete launching_next_batch"
        Write-WatchdogHealth $status $prep $loopProcs $botProcs $mjProcs $progressAge "batch_complete" "start_next_batch"
        Stop-JanQRuntime
        Start-JanQBatch
        exit 0
    }

    if ($status -and (@("failed", "failed_recovered", "account_finished", "account_prep_interrupted") -contains $status.state)) {
        Write-WatchdogLog "nonrunning_noncomplete_state=$($status.state) restarting_to_resume"
        Write-WatchdogHealth $status $prep $loopProcs $botProcs $mjProcs $progressAge "nonrunning_noncomplete_state" "restart_batch"
        Stop-JanQRuntime
        Start-JanQBatch
        exit 0
    }

    if ($prep -and $prep.active -eq $true) {
        Write-WatchdogLog "prep_active_but_runner_missing restarting_to_resume requestId=$($prep.requestId)"
        Write-WatchdogHealth $status $prep $loopProcs $botProcs $mjProcs $progressAge "prep_active_but_runner_missing" "restart_batch"
        Stop-JanQRuntime
        Start-JanQBatch
        exit 0
    }

    Write-WatchdogLog "no_runner_starting_batch"
    Write-WatchdogHealth $status $prep $loopProcs $botProcs $mjProcs $progressAge "no_runner" "start_batch"
    Stop-JanQRuntime
    Start-JanQBatch
} finally {
    if ($lock) {
        $lock.Close()
    }
}
