param(
    [int]$GameWidth = 1280,
    [int]$GameHeight = 720,
    [int]$TargetMjchip = 4000,
    [int]$BankruptcyMjchip = 9,
    [int]$ForcedBet = 10,
    [int]$MaxHandsPerAccount = 100000,
    [int]$MaxNormalHandsPerAccount = 0,
    [int]$MaxRuntimeSecondsPerAccount = 86400,
    [int]$LimitAccounts = 0,
    [string]$AccountsPath = "",
    [string]$StatusPath = "",
    [switch]$RerunTerminal,
    [switch]$ContinueOnError,
    [switch]$FreshGame,
    [switch]$MinimizeGame
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$gamePath = (Resolve-Path (Join-Path $root "sega_net_MJ\MJ\MJ.exe")).Path
$gameDirectory = Split-Path -Parent $gamePath
$bepInExCore = Join-Path $gameDirectory "BepInEx\core\BepInEx.dll"
$pluginPath = Join-Path $gameDirectory "BepInEx\plugins\JanqProbe.dll"
$doorstopDll = Join-Path $gameDirectory "winhttp.dll"
$doorstopConfig = Join-Path $gameDirectory "doorstop_config.ini"
$bepInExPackage = Join-Path $root "_runtime\deploy\bepinex_runtime.zip"

function Write-Step {
    param([string]$Message)
    Write-Host "[account-batch] $Message"
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

Ensure-BepInExRuntime

$existingBot = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -in @("python.exe", "pythonw.exe") -and
        ($_.CommandLine -match "janq_lab\.automation\.bot" -or $_.CommandLine -match "run_account_batch\.py")
    } |
    Select-Object -First 1
if ($null -ne $existingBot) {
    throw "Existing JanQ bot/batch process detected: PID $($existingBot.ProcessId)"
}

$runningGame = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "MJ.exe" -and
        [string]::Equals($_.ExecutablePath, $gamePath, [System.StringComparison]::OrdinalIgnoreCase)
    } |
    Select-Object -First 1

if ($FreshGame -and $null -ne $runningGame) {
    Stop-Process -Id $runningGame.ProcessId -Force
    Start-Sleep -Seconds 3
    $runningGame = $null
}

if ($null -eq $runningGame) {
    $gameArgs = @(
        "-screen-fullscreen", "0",
        "-screen-width", "$GameWidth",
        "-screen-height", "$GameHeight"
    )
    $windowStyle = if ($MinimizeGame) { "Minimized" } else { "Normal" }
    Start-Process `
        -FilePath $gamePath `
        -WorkingDirectory $gameDirectory `
        -ArgumentList $gameArgs `
        -WindowStyle $windowStyle
    Start-Sleep -Seconds 8
}

if (-not $AccountsPath) {
    $AccountsPath = Join-Path $root "_runtime\accounts\accounts.json"
}
if (-not $StatusPath) {
    $StatusPath = Join-Path $root "_runtime\batch\account_batch_status.json"
}

$env:PYTHONPATH = Join-Path $root "src"
$pythonExe = $null
$pythonCandidates = @(
    (Join-Path $env:ProgramFiles "Python312\python.exe"),
    (Join-Path $env:ProgramFiles "Python311\python.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Python312\python.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Python311\python.exe")
)
foreach ($candidate in $pythonCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
        $pythonExe = $candidate
        break
    }
}
if (-not $pythonExe) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike "*\WindowsApps\python.exe" } |
        Select-Object -First 1
    if ($pythonCommand) {
        $pythonExe = $pythonCommand.Source
    }
}
if (-not $pythonExe) {
    throw "Python 3.11+ is required. Install Python first; the WindowsApps python.exe placeholder is not enough."
}
$batchArgs = @(
    (Join-Path $root "scripts\run_account_batch.py"),
    "--accounts-path", "$AccountsPath",
    "--config", (Join-Path $root "automation.example.yaml"),
    "--events-path", (Join-Path $root "_runtime\logs\janq_events.jsonl"),
    "--bridge-dir", (Join-Path $root "_runtime\bridge"),
    "--sessions-dir", (Join-Path $root "_runtime\sessions"),
    "--status-path", "$StatusPath",
    "--target-mjchip", "$TargetMjchip",
    "--bankruptcy-mjchip", "$BankruptcyMjchip",
    "--forced-bet", "$ForcedBet",
    "--max-hands-per-account", "$MaxHandsPerAccount",
    "--max-runtime-seconds-per-account", "$MaxRuntimeSecondsPerAccount"
)
if ($MaxNormalHandsPerAccount -gt 0) {
    $batchArgs += @("--max-normal-hands-per-account", "$MaxNormalHandsPerAccount")
}
if ($LimitAccounts -gt 0) {
    $batchArgs += @("--limit-accounts", "$LimitAccounts")
}
if ($RerunTerminal) {
    $batchArgs += @("--rerun-terminal")
}
if ($ContinueOnError) {
    $batchArgs += @("--continue-on-error")
}

& $pythonExe @batchArgs
exit $LASTEXITCODE
