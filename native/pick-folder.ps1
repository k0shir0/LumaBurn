$ErrorActionPreference = 'Stop'
# A Python/Electron parent can inherit PowerShell 7 module paths. Load WinPS built-ins explicitly.
$windowsModules = [IO.Path]::Combine($PSHOME, 'Modules')
$env:PSModulePath = $windowsModules + [IO.Path]::PathSeparator + $env:PSModulePath
Import-Module ([IO.Path]::Combine($windowsModules, 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
Import-Module ([IO.Path]::Combine($windowsModules, 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Management.psd1')) -ErrorAction Stop
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Application]::EnableVisualStyles()
$dialog = New-Object Windows.Forms.FolderBrowserDialog
$owner = New-Object Windows.Forms.Form
try {
    $owner.TopMost = $true
    $owner.ShowInTaskbar = $false
    $dialog.Description = 'Choose a folder for LumaBurn'
    $dialog.ShowNewFolderButton = $true
    $path = $null
    if ($dialog.ShowDialog($owner) -eq [Windows.Forms.DialogResult]::OK) { $path = $dialog.SelectedPath }
    [Console]::Out.WriteLine((@{ path = $path } | ConvertTo-Json -Compress))
} finally { $dialog.Dispose(); $owner.Dispose() }
