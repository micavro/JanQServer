param(
    [string]$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path,
    [string]$TaskName = "JanQRegisterLoop5000",
    [int]$StartupWaitSeconds = 15,
    [int]$Count = 5000,
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
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$loopDir = Join-Path $rootPath "_runtime\register_janq_loop"
$wrapper = Join-Path $loopDir ("server_run_{0}.ps1" -f $Count)
$startScript = Join-Path $rootPath "start_register_janq_loop.ps1"
$builtPlugin = Join-Path $rootPath "plugin\JanqProbe\bin\Release\net472\JanqProbe.dll"
$deployedPlugin = Join-Path $rootPath "sega_net_MJ\MJ\BepInEx\plugins\JanqProbe.dll"

foreach ($requiredPath in ($startScript, $deployedPlugin)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required path does not exist: $requiredPath"
    }
}
New-Item -ItemType Directory -Force -Path $loopDir | Out-Null

$wrapperLog = Join-Path $loopDir "wrapper.log"
$runArgs = @(
    "-Count", "$Count",
    "-Bet", "$Bet",
    "-TargetMjchip", "$TargetMjchip",
    "-BankruptcyMjchip", "$BankruptcyMjchip",
    "-NicknamePrefix", "$NicknamePrefix",
    "-MaxAccountResumeFailures", "$MaxAccountResumeFailures",
    "-MaxPrepRestartsPerAccount", "$MaxPrepRestartsPerAccount"
)
if ($HiddenGame) { $runArgs += "-HiddenGame" }
if ($FreshGame) { $runArgs += "-FreshGame" }
if ($FreshPrep) { $runArgs += "-FreshPrep" }
if ($ContinueOnError) { $runArgs += "-ContinueOnError" }
$runArgsJson = $runArgs | ConvertTo-Json -Compress
$wrapperContent = @"
`$ErrorActionPreference = "Stop"
Set-Location -LiteralPath "$rootPath"
`$runArgs = '$runArgsJson' | ConvertFrom-Json
& "$startScript" @runArgs *>&1 | Tee-Object -FilePath "$wrapperLog" -Append
exit `$LASTEXITCODE
"@
$wrapperContent | Set-Content -LiteralPath $wrapper -Encoding UTF8

& schtasks.exe /End /TN $TaskName 2>$null | Out-Null
Start-Sleep -Seconds 3

$escapedRoot = [regex]::Escape($rootPath)
Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -in @("python.exe", "powershell.exe") -and
        $_.ProcessId -ne $PID -and
        $_.CommandLine -match $escapedRoot
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Get-Process -Name MJ -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 2
if (Test-Path -LiteralPath $builtPlugin) {
    Copy-Item -LiteralPath $builtPlugin -Destination $deployedPlugin -Force
}

$service = New-Object -ComObject "Schedule.Service"
$service.Connect()
$folder = $service.GetFolder("\")
$definition = $service.NewTask(0)
$definition.RegistrationInfo.Description = "JanQ register loop for $rootPath (SYSTEM)"
$definition.Principal.UserId = "SYSTEM"
$definition.Principal.LogonType = 5
$definition.Principal.RunLevel = 1
$definition.Settings.Enabled = $true
$definition.Settings.AllowDemandStart = $true
$definition.Settings.StartWhenAvailable = $true
$definition.Settings.DisallowStartIfOnBatteries = $false
$definition.Settings.StopIfGoingOnBatteries = $false
$definition.Settings.ExecutionTimeLimit = "PT0S"
$definition.Settings.MultipleInstances = 2

$trigger = $definition.Triggers.Create(8)
$trigger.Enabled = $true
$action = $definition.Actions.Create(0)
$action.Path = "powershell.exe"
$action.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""
$action.WorkingDirectory = $rootPath

$registered = $folder.RegisterTaskDefinition(
    $TaskName,
    $definition,
    6,
    $null,
    $null,
    5,
    $null
)
$null = $registered.Run($null)
Start-Sleep -Seconds $StartupWaitSeconds

$task = $folder.GetTask($TaskName)
$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -in @("python.exe", "powershell.exe", "MJ.exe") -and
        ($_.CommandLine -match $escapedRoot -or $_.ExecutablePath -like "$rootPath*")
    } |
    Select-Object ProcessId, ParentProcessId, SessionId, Name, CommandLine

[pscustomobject]@{
    taskName = $TaskName
    taskState = $task.State
    lastTaskResult = $task.LastTaskResult
    root = $rootPath
    pluginSha256 = (Get-FileHash $deployedPlugin -Algorithm SHA256).Hash.ToLowerInvariant()
    wrapper = $wrapper
    processes = @($processes)
    wrapperLog = @(
        Get-Content $wrapperLog -Tail 5 -ErrorAction SilentlyContinue |
            ForEach-Object { [string]$_ }
    )
} | ConvertTo-Json -Depth 5 -Compress
