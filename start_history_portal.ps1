param(
  [string]$HostName = '127.0.0.1',
  [int]$Port = 8199,
  [ValidateSet('Auto', 'Kill', 'Fail')]
  [string]$PortConflict = 'Auto',
  [int]$MaxPortScan = 30
)

function Get-ListeningConnectionsForPort {
  param([int]$TargetPort)

  try {
    return @(Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction Stop)
  }
  catch {
    return @()
  }
}

function Test-PortAvailable {
  param([int]$TargetPort)
  return (Get-ListeningConnectionsForPort -TargetPort $TargetPort).Count -eq 0
}

function Stop-ListenersOnPort {
  param([int]$TargetPort)

  $listeners = Get-ListeningConnectionsForPort -TargetPort $TargetPort
  if ($listeners.Count -eq 0) {
    return 0
  }

  $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
  $stopped = 0
  foreach ($pid in $pids) {
    if (-not $pid) { continue }
    try {
      $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pid" -ErrorAction SilentlyContinue
      if ($proc) {
        Write-Host "[history_portal] Stopping process on port ${TargetPort}: PID=$pid Name=$($proc.Name)"
      }
      Stop-Process -Id $pid -Force -ErrorAction Stop
      $stopped++
    }
    catch {
      Write-Warning "[history_portal] Failed to stop PID=$pid on port ${TargetPort}: $($_.Exception.Message)"
    }
  }

  Start-Sleep -Milliseconds 400
  return $stopped
}

function Find-NextAvailablePort {
  param(
    [int]$StartPort,
    [int]$MaxScan
  )

  $limit = [Math]::Max(1, $MaxScan)
  for ($i = 0; $i -le $limit; $i++) {
    $candidate = $StartPort + $i
    if (Test-PortAvailable -TargetPort $candidate) {
      return $candidate
    }
  }
  throw "No open port found in range $StartPort-$($StartPort + $limit)"
}

$selectedPort = $Port
$isRequestedPortFree = Test-PortAvailable -TargetPort $Port

if (-not $isRequestedPortFree) {
  switch ($PortConflict) {
    'Kill' {
      $stopped = Stop-ListenersOnPort -TargetPort $Port
      if ($stopped -eq 0 -and -not (Test-PortAvailable -TargetPort $Port)) {
        throw "Port $Port is busy and no listener could be stopped."
      }
      if (-not (Test-PortAvailable -TargetPort $Port)) {
        throw "Port $Port is still busy after attempting to stop listeners."
      }
      Write-Host "[history_portal] Port $Port was occupied. Reclaimed successfully."
      $selectedPort = $Port
    }
    'Auto' {
      $selectedPort = Find-NextAvailablePort -StartPort $Port -MaxScan $MaxPortScan
      if ($selectedPort -ne $Port) {
        Write-Host "[history_portal] Port $Port is busy. Using next available port: $selectedPort"
      }
    }
    'Fail' {
      throw "Port $Port is already in use. Re-run with -PortConflict Auto or -PortConflict Kill."
    }
  }
}

$env:HISTORY_PORTAL_HOST = $HostName
$env:HISTORY_PORTAL_PORT = "$selectedPort"

if (-not $env:USER_DB_PATH) {
  $env:USER_DB_PATH = "D:\Momi Forge\users.db"
}

Set-Location "D:\Momi Forge\history_portal"

if (-not (Test-Path '.\node_modules')) {
  Write-Host '[history_portal] Installing npm dependencies...'
  npm install
}

Write-Host "[history_portal] USER_DB_PATH=$($env:USER_DB_PATH)"
Write-Host "[history_portal] Starting on http://$HostName`:$selectedPort"
npm start
