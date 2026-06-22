param(
    [Parameter(Mandatory = $true)]
    [string]$DestinationRoot,
    [string]$SourceRoot = "",
    [switch]$RestartAfter,
    [int]$RestartCount = 5000,
    [int]$Bet = 50,
    [int]$TargetMjchip = 20000,
    [int]$BankruptcyMjchip = 49,
    [string]$NicknamePrefix = "Mica",
    [int]$MaxAccountResumeFailures = 5,
    [int]$MaxPrepRestartsPerAccount = 5,
    [switch]$HiddenGame = $true,
    [switch]$FreshGame,
    [switch]$FreshPrep,
    [switch]$ContinueOnError
)

$ErrorActionPreference = "Stop"
if (-not $SourceRoot) {
    $SourceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
} else {
    $SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
}

$DestinationRoot = [System.IO.Path]::GetFullPath($DestinationRoot)
if ([string]::Equals($SourceRoot.TrimEnd('\'), $DestinationRoot.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "SourceRoot and DestinationRoot are the same path; refusing to deploy onto itself."
}

New-Item -ItemType Directory -Force -Path $DestinationRoot | Out-Null

$excludeDirs = @(
    "_runtime",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__"
)
$excludeFiles = @("*.pyc", "*.pyo")

$args = @(
    $SourceRoot,
    $DestinationRoot,
    "/E",
    "/R:2",
    "/W:2",
    "/FFT",
    "/NP",
    "/XD"
) + $excludeDirs + @("/XF") + $excludeFiles

& robocopy @args | Out-Host
$code = $LASTEXITCODE
if ($code -ge 8) {
    throw "robocopy failed with exit code $code"
}

$deployDir = Join-Path $DestinationRoot "_runtime\deploy"
New-Item -ItemType Directory -Force -Path $deployDir | Out-Null
$manifest = [ordered]@{
    deployedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    sourceRoot = $SourceRoot
    destinationRoot = $DestinationRoot
    robocopyExitCode = $code
    excludedDirs = $excludeDirs
    excludedFiles = $excludeFiles
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $deployDir "last_deploy.json") -Encoding UTF8

if ($RestartAfter) {
    $restartScript = Join-Path $DestinationRoot "scripts\restart_register_loop_system.ps1"
    if (-not (Test-Path -LiteralPath $restartScript)) {
        throw "restart script not found after deploy: $restartScript"
    }
    $restartArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $restartScript,
        "-Root", $DestinationRoot,
        "-Count", "$RestartCount",
        "-Bet", "$Bet",
        "-TargetMjchip", "$TargetMjchip",
        "-BankruptcyMjchip", "$BankruptcyMjchip",
        "-NicknamePrefix", "$NicknamePrefix",
        "-MaxAccountResumeFailures", "$MaxAccountResumeFailures",
        "-MaxPrepRestartsPerAccount", "$MaxPrepRestartsPerAccount"
    )
    if ($HiddenGame) { $restartArgs += "-HiddenGame" }
    if ($FreshGame) { $restartArgs += "-FreshGame" }
    if ($FreshPrep) { $restartArgs += "-FreshPrep" }
    if ($ContinueOnError) { $restartArgs += "-ContinueOnError" }
    Start-Process -FilePath powershell.exe -ArgumentList $restartArgs -WorkingDirectory $DestinationRoot -WindowStyle Hidden | Out-Null
}

[pscustomobject]@{
    sourceRoot = $SourceRoot
    destinationRoot = $DestinationRoot
    deployedAt = $manifest.deployedAt
    restarted = [bool]$RestartAfter
    robocopyExitCode = $code
} | ConvertTo-Json -Depth 4
