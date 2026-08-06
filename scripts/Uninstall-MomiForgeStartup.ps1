[CmdletBinding()]
param()

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
    $elevated = Start-Process -FilePath $powerShellExe -Verb RunAs -ArgumentList $elevatedArguments -Wait -PassThru
    exit $elevated.ExitCode
}

$taskNames = @(
    'Momi Forge - Main App (boot)'
    'Momi Forge - History Portal (boot)'
    'Momi Forge - RunPod Manager (boot)'
)

foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Not installed: $taskName"
        continue
    }

    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed: $taskName"
}

Write-Host ''
Write-Host 'Momi Forge boot startup was removed.' -ForegroundColor Green
Write-Host 'Existing log files and application data were left untouched.'
