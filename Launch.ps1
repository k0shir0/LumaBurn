param([int]$Port = 9847, [switch]$NoWindow)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonCommand = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) { $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue }
if (-not $pythonCommand) { throw 'Python 3.11 or later is required. See README.md.' }
$appUrl = "http://127.0.0.1:$Port"
$alreadyRunning = $false
try {
    $state = Invoke-RestMethod "$appUrl/api/state" -TimeoutSec 2
    $alreadyRunning = [bool]$state.session_token -and [bool]$state.presets
} catch { }
if (-not $alreadyRunning) {
    $localPath = Join-Path $projectRoot '.local'
    [void](New-Item -ItemType Directory -Path $localPath -Force)
    Start-Process -FilePath $pythonCommand.Source -ArgumentList @('-m', 'lumaburn', '--no-browser', '--port', "$Port") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $localPath 'server.log') -RedirectStandardError (Join-Path $localPath 'server-error.log')
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $state = Invoke-RestMethod "$appUrl/api/state" -TimeoutSec 1
            if ($state.session_token) { $alreadyRunning = $true; break }
        } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $alreadyRunning) { throw 'The app could not start. Check .local/server-error.log or run python -m lumaburn.' }
}
if ($NoWindow) { Write-Output $appUrl; exit }
$edge = Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
if (Test-Path -LiteralPath $edge) {
    Start-Process -FilePath $edge -ArgumentList @("--app=$appUrl", '--window-size=1440,1000')
} else {
    Start-Process $appUrl
}
