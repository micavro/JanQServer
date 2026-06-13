param(
    [int]$MaxHands = 100,
    [int]$MaxRuntimeSeconds = 3600
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
    Start-Process `
        -FilePath $gamePath `
        -WorkingDirectory $gameDirectory `
        -WindowStyle Minimized
}

$env:PYTHONPATH = Join-Path $root "src"
& python -m janq_lab.automation.bot `
    --config (Join-Path $root "automation.example.yaml") `
    --mode plugin_live `
    --max-hands $MaxHands `
    --max-runtime-seconds $MaxRuntimeSeconds

exit $LASTEXITCODE
