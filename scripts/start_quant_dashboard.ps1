$uiDir = "D:\MyAI\My work team\Decided plan\ui\quant-dashboard"
$uiOut = "D:\MyAI\My work team\Decided plan\monitor\output\processes\ui.log"
$uiErr = "D:\MyAI\My work team\Decided plan\monitor\output\processes\ui.err.log"
$nodeExe = "C:\Program Files\nodejs\node.exe"

New-Item -ItemType Directory -Force -Path (Split-Path $uiOut) | Out-Null
try {
    Set-Content -Path $uiOut -Value "[UI] booting Quant Dashboard" -Encoding utf8 -ErrorAction Stop
} catch {
}
try {
    if (Test-Path $uiErr) {
        Remove-Item $uiErr -Force -ErrorAction Stop
    }
} catch {
}

Set-Location $uiDir
$env:NODE_ENV = "production"
$env:PORT = "8050"

& $nodeExe "dist/index.js" 1>> $uiOut 2>> $uiErr
