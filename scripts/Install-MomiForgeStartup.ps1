[CmdletBinding()]
param(
    [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    Write-Host 'Administrator permission is required. Opening the Windows UAC prompt...'
    $powerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $elevatedArguments = @(
        '-NoLogo'
        '-NoProfile'
        '-ExecutionPolicy'
        'Bypass'
        '-File'
        "`"$PSCommandPath`""
    )
    if ($StartNow) {
        $elevatedArguments += '-StartNow'
    }

    $elevated = Start-Process -FilePath $powerShellExe -Verb RunAs -ArgumentList $elevatedArguments -Wait -PassThru
    exit $elevated.ExitCode
}

$componentScript = Join-Path $PSScriptRoot 'Start-MomiForgeComponent.ps1'
if (-not (Test-Path -LiteralPath $componentScript -PathType Leaf)) {
    throw "Component launcher was not found at: $componentScript"
}

$components = [ordered]@{
    'Momi Forge - Main App (boot)' = 'App'
    'Momi Forge - History Portal (boot)' = 'HistoryPortal'
    'Momi Forge - RunPod Manager (boot)' = 'RunPodManager'
}

Write-Host 'Validating Momi Forge files and runtimes...'
foreach ($component in $components.Values) {
    & $componentScript -Component $component -ValidateOnly | Format-Table -AutoSize
}

$powerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$principal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

foreach ($entry in $components.GetEnumerator()) {
    $taskName = $entry.Key
    $component = $entry.Value
    $taskArguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$componentScript`" -Component $component"
    $action = New-ScheduledTaskAction -Execute $powerShellExe -Argument $taskArguments

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Starts the Momi Forge $component component at Windows boot before user sign-in and keeps it running after logout." `
        -Force | Out-Null

    Write-Host "Registered: $taskName"
}

if ($StartNow) {
    Write-Host 'Starting the tasks now...'
    foreach ($taskName in $components.Keys) {
        Start-ScheduledTask -TaskName $taskName
    }
}

Write-Host ''
Write-Host 'Momi Forge boot startup is installed.' -ForegroundColor Green
Write-Host 'The tasks run as SYSTEM, so no Windows account password or sign-in is required.'
if (-not $StartNow) {
    Write-Host 'They will start automatically on the next Windows restart.'
}
Write-Host 'Runtime logs: D:\Momi Forge\logs\startup'
