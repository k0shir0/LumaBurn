"""Run only safe native functions against temporary files; never load the entrypoint."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r"""
param([string]$NativePath, [string]$Fixture)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($NativePath, [ref]$tokens, [ref]$errors)
if ($errors) { throw ($errors | Out-String) }
foreach ($name in @('Wait-Gentle', 'Get-Sha256', 'Get-Manifest', 'Assert-ManifestUnchanged')) {
    $definition = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)
    if ($null -eq $definition) { throw "Missing safe function: $name" }
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Request-Value([string]$Name, $Default = $null) { return $Default }
function Write-ProgressJson([string]$Phase, $Progress, [string]$Message) { }
function Rejected($Manifest) {
    try { Assert-ManifestUnchanged $Fixture $Manifest; return $false } catch { return $true }
}
[void][IO.Directory]::CreateDirectory($Fixture)
$one = Join-Path $Fixture 'one.bin'
$two = Join-Path $Fixture 'two.bin'
[IO.File]::WriteAllBytes($one, [byte[]](1, 2, 3, 4))
$manifest = Get-Manifest $Fixture
$result = [ordered]@{ unchanged = -not (Rejected $manifest) }
$stamp = [IO.File]::GetLastWriteTimeUtc($one)
[IO.File]::WriteAllBytes($one, [byte[]](4, 3, 2, 1))
[IO.File]::SetLastWriteTimeUtc($one, $stamp)
$result.same_size = (Get-Item -LiteralPath $one).Length -eq 4
$result.same_stamp = [IO.File]::GetLastWriteTimeUtc($one) -eq $stamp
$result.changed = Rejected $manifest
[IO.File]::WriteAllBytes($one, [byte[]](1, 2, 3, 4))
[IO.File]::WriteAllBytes($two, [byte[]](5, 6, 7, 8))
$result.added = Rejected $manifest
$pair = Get-Manifest $Fixture
Remove-Item -LiteralPath $two
$result.removed = Rejected $pair
$script:Delays = @()
function Start-Sleep([int]$Milliseconds) { $script:Delays += $Milliseconds }
Wait-Gentle $true 0
Wait-Gentle $true 10
Wait-Gentle $true 40
Wait-Gentle $false 0
$result.delays = $script:Delays
$result | ConvertTo-Json -Compress
"""


class NativeGuardTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows PowerShell regression")
    def test_source_content_and_native_pacing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "guard.ps1"
            script.write_text(DRIVER, encoding="utf-8")
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(script), str(ROOT / "native" / "optical.ps1"), str(root / "files")],
                capture_output=True, text=True, encoding="utf-8-sig", errors="replace", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.strip())
        for check in ("unchanged", "same_size", "same_stamp", "changed", "added", "removed"):
            self.assertTrue(result[check], check)
        self.assertEqual(result["delays"], [16, 6])


if __name__ == "__main__":
    unittest.main()
