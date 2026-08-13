[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('App', 'HistoryPortal', 'RunPodManager')]
    [string]$Component,

    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonExe = Join-Path $root '.venv\Scripts\python.exe'
$nodeCandidates = @(
    @(
        (Join-Path $env:ProgramFiles 'nodejs\node.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'nodejs\node.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
)

if ($nodeCandidates.Count -gt 0) {
    $nodeExe = $nodeCandidates[0]
}
else {
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    $nodeExe = if ($nodeCommand) { $nodeCommand.Source } else { $null }
}

$historyDir = Join-Path $root 'history_portal'
$runPodBackendDir = Join-Path $root 'runpod_management\webapp\backend'
$runPodDistDir = Join-Path $root 'runpod_management\webapp\frontend\dist'
$logDir = Join-Path $root 'logs\startup'

function Assert-File {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label was not found at: $Path"
    }
}

function Assert-Directory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label was not found at: $Path"
    }
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }

        $pair = $trimmed -split '=', 2
        if ($pair.Count -eq 2 -and $pair[0].Trim() -eq $Name) {
            return $pair[1].Trim().Trim('"').Trim("'")
        }
    }

    return $null
}

function Resolve-PortalSigningSecret {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    # The Python app and both Node services must sign portal tokens with the
    # same value, and .env is the single source of truth for it.
    $secret = if ($env:HISTORY_PORTAL_SSO_SECRET) { $env:HISTORY_PORTAL_SSO_SECRET.Trim() } else { $null }
    if (-not $secret) {
        $secret = Get-DotEnvValue -Path (Join-Path $Root '.env') -Name 'HISTORY_PORTAL_SSO_SECRET'
    }

    if (-not $secret) {
        throw @'
HISTORY_PORTAL_SSO_SECRET is not set in .env. It signs the history portal and
RunPod management access tokens, so the services refuse to start without it.
Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"
'@
    }

    if ($secret -eq 'momi-forge-local-sso-secret') {
        throw @'
HISTORY_PORTAL_SSO_SECRET is still the placeholder that shipped in the
repository. That value is public, so anyone could forge an admin portal token.
Replace it with: python -c "import secrets; print(secrets.token_urlsafe(32))"
'@
    }

    return $secret
}

Assert-File -Path $pythonExe -Label 'Python virtual environment executable'
Assert-File -Path (Join-Path $root 'app.py') -Label 'Momi Forge app entry point'
Assert-File -Path (Join-Path $historyDir 'server.js') -Label 'History Portal entry point'
Assert-File -Path (Join-Path $runPodBackendDir 'src\server.js') -Label 'RunPod Manager entry point'
Assert-File -Path (Join-Path $runPodDistDir 'index.html') -Label 'RunPod Manager frontend build'
Assert-File -Path (Join-Path $root 'users.db') -Label 'Momi Forge user database'
Assert-Directory -Path (Join-Path $historyDir 'node_modules') -Label 'History Portal Node.js dependencies'
Assert-Directory -Path (Join-Path $runPodBackendDir 'node_modules') -Label 'RunPod Manager Node.js dependencies'

if (-not $nodeExe) {
    throw 'Node.js was not found. Install Node.js for all users before enabling boot startup.'
}

# These values mirror start_momi_forge.bat, but use absolute paths so they also
# work in the non-interactive SYSTEM session before a user signs in.
$env:USER_DB_PATH = Join-Path $root 'users.db'
$env:APP_SERVER_NAME = '0.0.0.0'
$env:APP_SERVER_PORT = '8188'
$env:APP_SSL_ENABLE = 'auto'
$env:APP_SSL_CERTFILE = Join-Path $root 'openssl\cert.pem'
$env:APP_SSL_KEYFILE = Join-Path $root 'openssl\key.pem'
$env:HISTORY_PORTAL_HOST = '0.0.0.0'
$env:HISTORY_PORTAL_PORT = '8199'
$env:HISTORY_PORTAL_URL = 'http://127.0.0.1:8199'
$env:HISTORY_PORTAL_USE_PROXY = '1'
$env:HISTORY_PORTAL_SSO_SECRET = Resolve-PortalSigningSecret -Root $root
$env:RUNPOD_MANAGEMENT_ROOT = Join-Path $root 'runpod_management'
$env:RUNPOD_MANAGEMENT_BACKEND_DIR = $runPodBackendDir
$env:RUNPOD_MANAGEMENT_DIST_DIR = $runPodDistDir
$env:RUNPOD_MANAGEMENT_API_PORT = '8843'
$env:RUNPOD_MANAGEMENT_API_UPSTREAM_URL = 'https://127.0.0.1:8843'
$env:WEBAPP_API_PORT = '8843'

# Per-poll RunPod trace logs (JSONL in trace_logs\), used to tell a queue wait
# apart from worker startup when a job sits in "Preparation". Unlike the values
# above these are only defaulted, so a machine-level RUNPOD_TRACE_DEBUG=0 turns
# tracing off for the boot service without editing this file.
if ([string]::IsNullOrWhiteSpace($env:RUNPOD_TRACE_DEBUG)) {
    $env:RUNPOD_TRACE_DEBUG = '1'
}
if ([string]::IsNullOrWhiteSpace($env:RUNPOD_TRACE_DIR)) {
    $env:RUNPOD_TRACE_DIR = Join-Path $root 'trace_logs'
}

$sslAvailable =
    (Test-Path -LiteralPath $env:APP_SSL_CERTFILE -PathType Leaf) -and
    (Test-Path -LiteralPath $env:APP_SSL_KEYFILE -PathType Leaf)
$env:APP_SCHEME = if ($sslAvailable) { 'https' } else { 'http' }
$env:WEBAPP_ORIGIN = "$($env:APP_SCHEME)://127.0.0.1:$($env:APP_SERVER_PORT)"

# Node is installed machine-wide on this host. Prepending both runtime folders
# also makes subprocess lookup deterministic when this script runs as SYSTEM.
$env:Path = "$(Split-Path -Parent $nodeExe);$(Split-Path -Parent $pythonExe);$($env:Path)"

switch ($Component) {
    'App' {
        $executable = $pythonExe
        $argumentList = @('app.py')
        $workingDirectory = $root
        $logPrefix = 'momi-forge-app'
    }
    'HistoryPortal' {
        $executable = $nodeExe
        $argumentList = @('server.js')
        $workingDirectory = $historyDir
        $logPrefix = 'history-portal'
    }
    'RunPodManager' {
        $executable = $nodeExe
        $argumentList = @('src/server.js')
        $workingDirectory = $runPodBackendDir
        $logPrefix = 'runpod-manager-backend'
    }
}

if ($ValidateOnly) {
    [pscustomobject]@{
        Component = $Component
        Executable = $executable
        WorkingDirectory = $workingDirectory
        Status = 'Ready'
    }
    return
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$supervisorLog = Join-Path $logDir "$logPrefix-supervisor.log"

if ($Component -eq 'App') {
    # Nobody sees a console banner in the SYSTEM session, so record whether
    # tracing is on. The app prunes trace files to RUNPOD_TRACE_RETENTION_FILES.
    $traceState = if ($env:RUNPOD_TRACE_DEBUG -eq '0') {
        'off'
    }
    else {
        "on -> $($env:RUNPOD_TRACE_DIR)"
    }
    Add-Content -LiteralPath $supervisorLog -Encoding UTF8 -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss K')] RunPod trace logs $traceState."
}

while ($true) {
    # Keep the latest 30 process runs for this component. A port conflict or a
    # repeated crash must not be allowed to create startup logs indefinitely.
    Get-ChildItem -LiteralPath $logDir -Filter "$logPrefix-*.out.log" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 30 |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $logDir -Filter "$logPrefix-*.err.log" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 30 |
        Remove-Item -Force -ErrorAction SilentlyContinue

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdoutLog = Join-Path $logDir "$logPrefix-$stamp.out.log"
    $stderrLog = Join-Path $logDir "$logPrefix-$stamp.err.log"
    $startedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'
    Add-Content -LiteralPath $supervisorLog -Encoding UTF8 -Value "[$startedAt] Starting $Component."

    try {
        $process = Start-Process `
            -FilePath $executable `
            -ArgumentList $argumentList `
            -WorkingDirectory $workingDirectory `
            -NoNewWindow `
            -PassThru `
            -Wait `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog

        $exitCode = $process.ExitCode
        $stoppedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'
        Add-Content -LiteralPath $supervisorLog -Encoding UTF8 -Value "[$stoppedAt] $Component exited with code $exitCode. Restarting in 10 seconds."
    }
    catch {
        $failedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'
        Add-Content -LiteralPath $supervisorLog -Encoding UTF8 -Value "[$failedAt] $Component failed to start: $($_.Exception.Message). Retrying in 10 seconds."
    }

    Start-Sleep -Seconds 10
}
