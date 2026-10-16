# Downloads the DVD authoring engine locally; does not run the DVDStyler installer.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$projectRoot = Split-Path $PSScriptRoot -Parent
$dependencyPath = Join-Path $projectRoot '.local\dependencies'
[void](New-Item -ItemType Directory -Path $dependencyPath -Force)
$installerPath = Join-Path $dependencyPath 'DVDStyler-3.2.1-win64.exe'
$extractorZip = Join-Path $dependencyPath 'innoextract-1.9-windows.zip'
$downloads = @(
    @{ Path = $installerPath; Url = 'https://sourceforge.net/projects/dvdstyler/files/dvdstyler/3.2.1/DVDStyler-3.2.1-win64.exe/download'; Hash = '99b74055e9b5cceae29b153158563e454905531c966f3cdbe98ea390a6a0e713' },
    @{ Path = $extractorZip; Url = 'https://github.com/dscharrer/innoextract/releases/download/1.9/innoextract-1.9-windows.zip'; Hash = '6989342c9b026a00a72a38f23b62a8e6a22cc5de69805cf47d68ac2fec993065' }
)
foreach ($download in $downloads) {
    if (-not (Test-Path -LiteralPath $download.Path)) {
        Write-Host "Downloading $([IO.Path]::GetFileName($download.Path))"
        Invoke-WebRequest -Uri $download.Url -OutFile $download.Path
    }
    if ((Get-FileHash -LiteralPath $download.Path -Algorithm SHA256).Hash -ne $download.Hash) {
        throw "Checksum mismatch: $($download.Path). No downloaded executable was run."
    }
}
$extractorPath = Join-Path $dependencyPath 'innoextract'
Expand-Archive -LiteralPath $extractorZip -DestinationPath $extractorPath -Force
$destinationPath = Join-Path $projectRoot 'tools\DVDStyler'
[void](New-Item -ItemType Directory -Path $destinationPath -Force)
& (Join-Path $extractorPath 'innoextract.exe') --silent --extract --output-dir $destinationPath $installerPath
if ($LASTEXITCODE -ne 0) { throw 'Extracting DVD authoring tools failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $destinationPath 'app\bin\dvdauthor.exe'))) { throw 'DVD authoring engine not found after extraction.' }
Write-Host 'DVD authoring tools are ready. FFmpeg/FFprobe must also be on PATH.'
