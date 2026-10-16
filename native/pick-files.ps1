$ErrorActionPreference = 'Stop'
# A Python/Electron parent can inherit PowerShell 7 module paths. Load WinPS built-ins explicitly.
$windowsModules = [IO.Path]::Combine($PSHOME, 'Modules')
$env:PSModulePath = $windowsModules + [IO.Path]::PathSeparator + $env:PSModulePath
Import-Module ([IO.Path]::Combine($windowsModules, 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
Import-Module ([IO.Path]::Combine($windowsModules, 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Management.psd1')) -ErrorAction Stop
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Application]::EnableVisualStyles()
$dialog = New-Object Windows.Forms.OpenFileDialog
$owner = New-Object Windows.Forms.Form
try {
    $owner.TopMost = $true
    $owner.ShowInTaskbar = $false
    $dialog.Title = 'Choose files for LumaBurn'
    $dialog.Filter = 'All files (*.*)|*.*'
    $dialog.Multiselect = $true
    $dialog.CheckFileExists = $true
    $paths = @()
    if ($dialog.ShowDialog($owner) -eq [Windows.Forms.DialogResult]::OK) { $paths = @($dialog.FileNames) }
    [Console]::Out.WriteLine((@{ paths = $paths } | ConvertTo-Json -Compress))
} finally { $dialog.Dispose(); $owner.Dispose() }
