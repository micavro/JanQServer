param(
    [string]$Root = ""
)

$ErrorActionPreference = "Continue"
if (-not $Root) {
    $Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
} else {
    $Root = (Resolve-Path -LiteralPath $Root).Path
}

$runtime = Join-Path $Root "_runtime"
$loopDir = Join-Path $runtime "register_janq_loop"
$statusPath = Join-Path $loopDir "status.json"
$healthPath = Join-Path $loopDir "health.json"
$launchArgsPath = Join-Path $loopDir "launch_args.json"
$prepPath = Join-Path $runtime "account_prep\status.json"
$eventsPath = Join-Path $runtime "logs\janq_events.jsonl"
$sessionsDir = Join-Path $runtime "sessions"
$accountsPath = Join-Path $runtime "accounts\accounts.json"
$interruptedPath = Join-Path $runtime "accounts\interrupted_accounts.jsonl"
$gamePath = Join-Path $Root "sega_net_MJ\MJ\MJ.exe"

function Read-JsonFile($Path) {
    try {
        if (Test-Path -LiteralPath $Path) {
            return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        }
    } catch {}
    return $null
}

function Test-CommandLineInWorkspace($CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    $workspacePattern = [regex]::Escape($Root.TrimEnd('\')) + '(\\|"|\s|$)'
    return $CommandLine -match $workspacePattern
}

function Get-ProcessCount($Kind) {
    if ($Kind -eq "mj") {
        $resolved = $null
        try { $resolved = (Resolve-Path -LiteralPath $gamePath).Path } catch { return 0 }
        return @(
            Get-CimInstance Win32_Process -Filter "Name = 'MJ.exe'" |
                Where-Object { [string]::Equals($_.ExecutablePath, $resolved, [System.StringComparison]::OrdinalIgnoreCase) }
        ).Count
    }
    $pattern = if ($Kind -eq "loop") { "run_register_janq_loop.py" } else { "janq_lab.automation.bot" }
    return @(
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
            Where-Object { (Test-CommandLineInWorkspace $_.CommandLine) -and $_.CommandLine -like "*$pattern*" }
    ).Count
}

function Get-LastWriteInfo($Path) {
    try {
        if (Test-Path -LiteralPath $Path) {
            $item = Get-Item -LiteralPath $Path
            return [pscustomobject]@{
                path = $Path
                lastWrite = $item.LastWriteTime.ToString("s")
                ageSeconds = [math]::Round(((Get-Date) - $item.LastWriteTime).TotalSeconds, 1)
                bytes = $item.Length
            }
        }
    } catch {}
    return $null
}

function Get-AccountStatusCounts {
    $result = @{}
    try {
        if (-not (Test-Path -LiteralPath $accountsPath)) { return $result }
        $raw = Get-Content -LiteralPath $accountsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $rows = if ($raw -is [array]) { $raw } elseif ($raw.accounts) { $raw.accounts } else { @() }
        foreach ($row in @($rows)) {
            $status = [string]$row.status
            if (-not $status) { $status = "<none>" }
            if (-not $result.ContainsKey($status)) { $result[$status] = 0 }
            $result[$status] += 1
        }
    } catch {}
    return $result
}

$status = Read-JsonFile $statusPath
$prep = Read-JsonFile $prepPath
$health = Read-JsonFile $healthPath
$launchArgs = Read-JsonFile $launchArgsPath
$latestSession = Get-ChildItem -LiteralPath $sessionsDir -Filter *.jsonl -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$interruptedTail = @()
try {
    if (Test-Path -LiteralPath $interruptedPath) {
        $interruptedTail = Get-Content -LiteralPath $interruptedPath -Tail 20 -Encoding UTF8 |
            ForEach-Object { try { $_ | ConvertFrom-Json } catch { $null } } |
            Where-Object { $null -ne $_ }
    }
} catch {}

$alerts = @()
$loopCount = Get-ProcessCount "loop"
$botCount = Get-ProcessCount "bot"
$mjCount = Get-ProcessCount "mj"
$eventsInfo = Get-LastWriteInfo $eventsPath
$sessionInfo = if ($latestSession) { Get-LastWriteInfo $latestSession.FullName } else { $null }
if ($loopCount -eq 0) { $alerts += "loop_missing" }
if ($mjCount -eq 0) { $alerts += "mj_missing" }
if ($status -and $status.state -ne "complete" -and $eventsInfo -and $eventsInfo.ageSeconds -gt 4500) { $alerts += "events_stale" }
if ($prep -and $prep.active -eq $true -and $prep.error) { $alerts += "prep_error" }

[pscustomobject]@{
    checkedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    root = $Root
    alerts = $alerts
    processes = [pscustomobject]@{
        loop = $loopCount
        bot = $botCount
        mj = $mjCount
    }
    loopStatus = $status
    prepStatus = if ($prep) {
        [pscustomobject]@{
            requestId = $prep.requestId
            nickname = $prep.nickname
            active = $prep.active
            stage = $prep.stage
            accountCaptured = $prep.accountCaptured
            error = $prep.error
            updatedAt = $prep.updatedAt
        }
    } else { $null }
    health = $health
    launchArgs = $launchArgs
    files = [pscustomobject]@{
        status = Get-LastWriteInfo $statusPath
        health = Get-LastWriteInfo $healthPath
        launchArgs = Get-LastWriteInfo $launchArgsPath
        prep = Get-LastWriteInfo $prepPath
        events = $eventsInfo
        latestSession = $sessionInfo
        interrupted = Get-LastWriteInfo $interruptedPath
    }
    accountStatusCounts = Get-AccountStatusCounts
    recentInterrupted = $interruptedTail
} | ConvertTo-Json -Depth 12
