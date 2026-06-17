param(
    [int]$MaxHands = 100,
    [int]$MaxRuntimeSeconds = 3600,
    [int]$GameWidth = 1280,
    [int]$GameHeight = 720,
    [int]$TargetMjchip = 1000000,
    [int]$ForcedBet = 0,
    [switch]$MinimizeGame
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$gamePath = (Resolve-Path (Join-Path $root "sega_net_MJ\MJ\MJ.exe")).Path
$gameDirectory = Split-Path -Parent $gamePath

$running = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "MJ.exe" -and
        [string]::Equals($_.ExecutablePath, $gamePath, [System.StringComparison]::OrdinalIgnoreCase)
    } |
    Select-Object -First 1

if ($null -eq $running) {
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
}

$env:PYTHONPATH = Join-Path $root "src"
$botArgs = @(
    "-m", "janq_lab.automation.bot",
    "--config", (Join-Path $root "automation.example.yaml"),
    "--mode", "plugin_live",
    "--max-hands", "$MaxHands",
    "--max-runtime-seconds", "$MaxRuntimeSeconds",
    "--target-mjchip", "$TargetMjchip"
)
if ($ForcedBet -gt 0) {
    $botArgs += @("--forced-bet", "$ForcedBet")
}

& python @botArgs

exit $LASTEXITCODE
