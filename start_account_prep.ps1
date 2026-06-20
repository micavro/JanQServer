param(
    [Alias("n")]
    [ValidateRange(1, 100)]
    [int]$Count = 1,
    [int]$TimeoutSeconds = 7200,
    [int]$GameWidth = 1600,
    [int]$GameHeight = 900,
    [string]$Nickname = "",
    [switch]$ShowGame,
    [switch]$SkipBuild,
    [switch]$Fresh,
    [switch]$Resume,
    [switch]$KeepGameBetweenRuns,
    [switch]$LeaveGameOpen
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

$gamePath = (Resolve-Path (Join-Path $root "sega_net_MJ\MJ\MJ.exe")).Path
$gameDirectory = Split-Path -Parent $gamePath
$bepInExCore = Join-Path $gameDirectory "BepInEx\core\BepInEx.dll"
$pluginPath = Join-Path $gameDirectory "BepInEx\plugins\JanqProbe.dll"
$bepInExLog = Join-Path $gameDirectory "BepInEx\LogOutput.log"
$doorstopDll = Join-Path $gameDirectory "winhttp.dll"
$doorstopConfig = Join-Path $gameDirectory "doorstop_config.ini"
$bepInExPackage = Join-Path $root "_runtime\deploy\bepinex_runtime.zip"
$requestPath = Join-Path $root "_runtime\account_prep\request.json"
$statusPath = Join-Path $root "_runtime\account_prep\status.json"
$scriptPid = $PID
$pythonExe = $null

function Write-Step {
    param([string]$Message)
    Write-Host "[account-prep] $Message"
}

function Ensure-BepInExRuntime {
    if (
        (Test-Path -LiteralPath $bepInExCore) -and
        (Test-Path -LiteralPath $pluginPath) -and
        (Test-Path -LiteralPath $doorstopDll) -and
        (Test-Path -LiteralPath $doorstopConfig)
    ) {
        return
    }

    if (Test-Path -LiteralPath $bepInExPackage) {
        Write-Step "restoring BepInEx runtime from $bepInExPackage"
        Expand-Archive -LiteralPath $bepInExPackage -DestinationPath $gameDirectory -Force
    }

    $missing = @()
    foreach ($path in @($bepInExCore, $pluginPath, $doorstopDll, $doorstopConfig)) {
        if (-not (Test-Path -LiteralPath $path)) {
            $missing += $path
        }
    }
    if ($missing.Count -gt 0) {
        throw "BepInEx runtime is missing under $gameDirectory. Missing: $($missing -join ', '). If antivirus removed these files, add an exclusion for $root and rerun."
    }
}

function Resolve-PythonExe {
    $pythonCandidates = @(
        (Join-Path $env:ProgramFiles "Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Python312\python.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Python311\python.exe")
    )
    foreach ($candidate in $pythonCandidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike "*\WindowsApps\python.exe" } |
        Select-Object -First 1
    if ($pythonCommand) {
        return $pythonCommand.Source
    }
    throw "Python 3.11+ is required. Install Python first; the WindowsApps python.exe placeholder is not enough."
}

function Resolve-DotNetExe {
    $dotnetCommand = Get-Command dotnet.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($dotnetCommand) {
        return $dotnetCommand.Source
    }

    $dotnetCandidates = @(
        (Join-Path $env:ProgramFiles "dotnet\dotnet.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "dotnet\dotnet.exe")
    )
    foreach ($candidate in $dotnetCandidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    throw ".NET SDK is required to build JanqProbe. Install Microsoft.DotNet.SDK.8 or rerun with -SkipBuild when JanqProbe.dll already exists."
}

function Get-CopiedMjProcess {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "MJ.exe" -and
            [string]::Equals($_.ExecutablePath, $gamePath, [System.StringComparison]::OrdinalIgnoreCase)
        } |
        Select-Object -First 1
}

function Stop-CopiedMjProcess {
    $running = Get-CopiedMjProcess
    if ($null -ne $running) {
        Write-Step "stopping copied MJ client (pid $($running.ProcessId))"
        Stop-Process -Id $running.ProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

function Stop-StaleAccountPrepMonitors {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.ProcessId -ne $scriptPid -and
            $_.CommandLine -match [regex]::Escape((Join-Path $root "scripts\run_account_prep.py"))
        } |
        ForEach-Object {
            Write-Step "stopping stale account-prep monitor (pid $($_.ProcessId))"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function Archive-AccountPrepState {
    param(
        [string]$Reason,
        [string]$DirectoryName
    )
    $archiveDirectory = Join-Path $root "_runtime\account_prep\$DirectoryName"
    New-Item -ItemType Directory -Force -Path $archiveDirectory | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    foreach ($path in @($requestPath, $statusPath)) {
        if (Test-Path $path) {
            Move-Item -LiteralPath $path -Destination (Join-Path $archiveDirectory "$([IO.Path]::GetFileName($path)).$stamp") -Force
        }
    }
    Write-Step $Reason
}

function Archive-StalePreRegistrationState {
    if ($Fresh -or $Resume -or $null -ne (Get-CopiedMjProcess) -or -not (Test-Path $requestPath) -or -not (Test-Path $statusPath)) {
        return
    }
    try {
        $status = Get-Content $statusPath -Raw | ConvertFrom-Json
        if ($status.active -and -not $status.accountCaptured) {
            Archive-AccountPrepState "archived stale pre-registration request; use -Resume to keep it" "archived_stale"
        }
    } catch {
        Write-Warning "Could not inspect/archive stale account-prep state: $($_.Exception.Message)"
    }
}

function Start-CopiedMjProcessIfNeeded {
    $running = Get-CopiedMjProcess
    if ($null -eq $running) {
        try {
            if (Test-Path $bepInExLog) {
                Remove-Item -LiteralPath $bepInExLog -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Warning "Could not clear old BepInEx log: $($_.Exception.Message)"
        }
        $gameArgs = @(
            "-screen-fullscreen", "0",
            "-screen-width", "$GameWidth",
            "-screen-height", "$GameHeight",
            "-force-d3d11",
            "-force-gfx-direct"
        )
        $windowStyle = if ($ShowGame) { "Normal" } else { "Minimized" }
        Write-Step "starting copied MJ client ($windowStyle)"
        Start-Process `
            -FilePath $gamePath `
            -WorkingDirectory $gameDirectory `
            -ArgumentList $gameArgs `
            -WindowStyle $windowStyle
    } else {
        Write-Step "copied MJ client is already running (pid $($running.ProcessId))"
    }
}

function Wait-JanqProbeLoaded {
    Write-Step "waiting for JanqProbe to load"
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $running = Get-CopiedMjProcess
        if ($null -eq $running) {
            throw "MJ.exe exited before JanqProbe loaded. Check $bepInExLog and the Unity Player.log."
        }
        if (Test-Path $bepInExLog) {
            $tail = Get-Content $bepInExLog -Tail 80 -ErrorAction SilentlyContinue
            if ($tail -match "JanQ Probe loaded") {
                Write-Step "JanqProbe loaded"
                return
            }
        }
        Start-Sleep -Seconds 1
    }

    Write-Warning "Timed out waiting for JanqProbe load marker. Last BepInEx lines:"
    if (Test-Path $bepInExLog) {
        Get-Content $bepInExLog -Tail 80
    }
    exit 3
}

function Resolve-RunNickname {
    param([int]$Iteration)
    if ([string]::IsNullOrWhiteSpace($Nickname)) {
        return ""
    }
    if ($Count -le 1) {
        return $Nickname
    }
    $suffix = "{0:D2}" -f $Iteration
    $maxBaseLength = [Math]::Max(1, 14 - $suffix.Length)
    $base = $Nickname.Trim()
    if ($base.Length -gt $maxBaseLength) {
        $base = $base.Substring(0, $maxBaseLength)
    }
    return $base + $suffix
}

function Run-OneAccountPrep {
    param([int]$Iteration)
    Start-CopiedMjProcessIfNeeded
    Wait-JanqProbeLoaded

    $prepArgs = @(
        (Join-Path $root "scripts\run_account_prep.py"),
        "--timeout-seconds", "$TimeoutSeconds"
    )
    $runNickname = Resolve-RunNickname $Iteration
    if (-not [string]::IsNullOrWhiteSpace($runNickname)) {
        $prepArgs += @("--nickname", $runNickname)
    }

    Write-Step "starting account registration and Yakuhime reward collection ($Iteration/$Count)"
    Write-Step "do not click the game window while it is running; monitor progress in this console"
    & $pythonExe @prepArgs
    $script:LastPrepExitCode = $LASTEXITCODE
}

Ensure-BepInExRuntime

$pythonExe = Resolve-PythonExe
Write-Step "using Python: $pythonExe"
Stop-StaleAccountPrepMonitors

if ($Fresh) {
    Archive-AccountPrepState "archived old request/status because -Fresh was specified" "archived_manual"
}

Archive-StalePreRegistrationState

if (-not $SkipBuild) {
    Write-Step "building JanqProbe"
    $dotnetExe = Resolve-DotNetExe
    & $dotnetExe build (Join-Path $root "plugin\JanqProbe\JanqProbe.csproj") -c Release
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path $pluginPath)) {
    throw "JanqProbe.dll was not found at $pluginPath. Build the plugin first, or run without -SkipBuild."
}

Write-Step "requested account-prep runs: $Count"
$exitCode = 0
for ($iteration = 1; $iteration -le $Count; $iteration += 1) {
    Run-OneAccountPrep $iteration
    $exitCode = $script:LastPrepExitCode
    Write-Step "account-prep run $iteration/$Count exited with code $exitCode"
    if ($exitCode -ne 0) {
        Write-Warning "Stopping loop because run $iteration failed."
        Write-Step "accounts file: $(Join-Path $root "_runtime\accounts\accounts.json")"
        Write-Step "latest screenshot while active: $(Join-Path $root "_runtime\account_prep\screenshots\latest.png")"
        exit $exitCode
    }
    if ($iteration -lt $Count -and -not $KeepGameBetweenRuns) {
        Stop-CopiedMjProcess
    }
}

if (-not $LeaveGameOpen) {
    Stop-CopiedMjProcess
}

Write-Step "all requested account-prep runs completed"
Write-Step "accounts file: $(Join-Path $root "_runtime\accounts\accounts.json")"
Write-Step "latest screenshot while active: $(Join-Path $root "_runtime\account_prep\screenshots\latest.png")"
exit $exitCode
