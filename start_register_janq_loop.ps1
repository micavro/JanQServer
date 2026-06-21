param(
    [Alias("n")]
    [ValidateRange(1, 10000)]
    [int]$Count = 1,
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
    [ValidateSet("public", "greedy", "route_ev", "route_ev2")]
    [string]$Strategy = "route_ev",
    [switch]$ShowGame,
    [switch]$HiddenGame,
    [switch]$FreshGame,
    [switch]$FreshPrep,
    [switch]$NoResumeStopped,
    [switch]$ContinueOnError
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

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

$pythonExe = Resolve-PythonExe
$env:PYTHONPATH = Join-Path $root "src"

$loopArgs = @(
    (Join-Path $root "scripts\run_register_janq_loop.py"),
    "--count", "$Count",
    "--bet", "$Bet",
    "--target-mjchip", "$TargetMjchip",
    "--bankruptcy-mjchip", "$BankruptcyMjchip",
    "--game-width", "$GameWidth",
    "--game-height", "$GameHeight",
    "--nickname-prefix", "$NicknamePrefix",
    "--prep-timeout-seconds", "$PrepTimeoutSeconds",
    "--prep-loading-stall-seconds", "$PrepLoadingStallSeconds",
    "--prep-generic-stall-seconds", "$PrepGenericStallSeconds",
    "--bot-max-hands", "$BotMaxHands",
    "--bot-max-runtime-seconds", "$BotMaxRuntimeSeconds",
    "--exit-timeout-seconds", "$ExitTimeoutSeconds",
    "--strategy", "$Strategy"
)

if ($PrepMaxStories -gt 0) {
    $loopArgs += @("--prep-max-stories", "$PrepMaxStories")
}

if ($ShowGame) {
    $loopArgs += "--show-game"
}
if ($HiddenGame) {
    $loopArgs += "--hidden-game"
}
if ($FreshGame) {
    $loopArgs += "--fresh-game"
}
if ($FreshPrep) {
    $loopArgs += "--fresh-prep"
}
if ($NoResumeStopped) {
    $loopArgs += "--no-resume-stopped"
}
if ($ContinueOnError) {
    $loopArgs += "--continue-on-error"
}

& $pythonExe @loopArgs
exit $LASTEXITCODE
