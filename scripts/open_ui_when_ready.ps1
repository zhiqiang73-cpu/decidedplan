$Root = Split-Path -Parent $PSScriptRoot
$HostName = "127.0.0.1"
$Port = 8050
$TimeoutSeconds = 180
$Url = "http://127.0.0.1:8050/"
$LogPath = Join-Path $Root "monitor\output\open_ui_when_ready.log"

function Write-Log {
    param([string]$Message)
    try {
        $logDir = Split-Path -Parent $LogPath
        if (-not (Test-Path $logDir)) {
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        }
        Add-Content -Path $LogPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Message" -Encoding UTF8
    } catch {
    }
}

Write-Log "PS watcher started for $Url timeout=${TimeoutSeconds}s"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)

while ((Get-Date) -lt $deadline) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(500) -and $client.Connected) {
            $client.EndConnect($async)
            Write-Log "Port became reachable: ${HostName}:$Port"
            Start-Sleep -Seconds 1
            Start-Process $Url
            Write-Log "Opened via Start-Process: $Url"
            exit 0
        }
    } catch {
    } finally {
        $client.Close()
    }
    Start-Sleep -Milliseconds 500
}

Write-Log "Timed out waiting for ${HostName}:$Port"
exit 0
