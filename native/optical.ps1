param(
    [Parameter(Mandatory = $true)][ValidateSet('inspect', 'burn', 'image', 'verify')][string]$Action,
    [Parameter(Mandatory = $true)][string]$RequestPath
)

$ErrorActionPreference = 'Stop'
# A Python/Electron parent can inherit PowerShell 7 module paths. Load WinPS built-ins explicitly.
$windowsModules = [IO.Path]::Combine($PSHOME, 'Modules')
$env:PSModulePath = $windowsModules + [IO.Path]::PathSeparator + $env:PSModulePath
Import-Module ([IO.Path]::Combine($windowsModules, 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1')) -ErrorAction Stop
Import-Module ([IO.Path]::Combine($windowsModules, 'Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Management.psd1')) -ErrorAction Stop
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:ComObjects = New-Object 'System.Collections.Generic.List[object]'
$script:Burned = $false
$script:WriteStarted = $false

function Write-Json($Value) {
    [Console]::Out.WriteLine(($Value | ConvertTo-Json -Depth 12 -Compress))
}
function Write-ProgressJson([string]$Phase, $Progress, [string]$Message) {
    Write-Json @{ phase = $Phase; progress = $Progress; message = $Message }
}
function Request-Value([string]$Name, $Default = $null) {
    $property = $script:Request.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}
function New-Com([string]$ProgId) {
    $object = New-Object -ComObject $ProgId
    $script:ComObjects.Add($object)
    return ,$object
}
function Track-Com($Object) {
    $script:ComObjects.Add($Object)
    return ,$Object
}
function Release-Com {
    for ($index = $script:ComObjects.Count - 1; $index -ge 0; $index--) {
        $object = $script:ComObjects[$index]
        if ([Runtime.InteropServices.Marshal]::IsComObject($object)) {
            try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($object) } catch { }
        }
    }
    $script:ComObjects.Clear()
}
function New-Recorder([string]$Id) {
    if ([string]::IsNullOrWhiteSpace($Id)) { throw 'Choose an optical recorder.' }
    $recorder = New-Com 'IMAPI2.MsftDiscRecorder2'
    $recorder.InitializeDiscRecorder($Id)
    return ,$recorder
}
function New-Writer($Recorder) {
    $writer = New-Com 'IMAPI2.MsftDiscFormat2Data'
    $writer.Recorder = $Recorder
    $writer.ClientName = 'LumaBurn'
    return ,$writer
}
function Recorder-Letter($Recorder) {
    foreach ($path in @($Recorder.VolumePathNames)) {
        if ([string]$path -match '^[A-Za-z]:') { return ([string]$path).Substring(0, 2).ToUpperInvariant() }
    }
    return $null
}
function Assert-BlankMedia($Writer, $Recorder, [long]$ImageBlocks = 0, [bool]$DvdVideo = $false) {
    if (-not $Writer.IsRecorderSupported($Recorder)) { throw 'This recorder does not support data burning.' }
    if (-not $Writer.IsCurrentMediaSupported($Recorder)) { throw 'The inserted disc is not supported for data burning.' }
    $status = [int]$Writer.CurrentMediaStatus
    if (($status -band 2) -eq 0 -or -not $Writer.MediaHeuristicallyBlank) {
        throw 'Only blank discs are accepted. LumaBurn does not erase, append to, or overwrite discs.'
    }
    if (($status -band 0xFC00) -ne 0 -or [int]$Writer.WriteProtectStatus -ne 0) {
        throw ('The disc is protected or requires recovery (media status 0x{0:X}). No disc changes were made.' -f $status)
    }
    $mediaType = [int]$Writer.CurrentPhysicalMediaType
    if ($DvdVideo -and $mediaType -notin @(5, 6, 7, 8, 9, 10, 11, 13)) { throw 'DVD-Video requires DVD media.' }
    $freeBlocks = [long]$Writer.FreeSectorsOnMedia
    if ($freeBlocks -le 0) { throw 'The drive reports no writable space.' }
    if ($ImageBlocks -gt $freeBlocks) { throw 'The completed disc image does not fit on the inserted disc.' }
    return @{ status = $status; media_type = $mediaType; free_blocks = $freeBlocks }
}
function Wait-Gentle([bool]$Gentle, [long]$ElapsedMilliseconds) {
    # Gentle mode caps app-controlled I/O near 64 MiB/s (one 1 MiB block per 16 ms),
    # but an operation that already took at least that interval is not delayed again.
    if (-not $Gentle) { return }
    $remaining = 16 - [int]$ElapsedMilliseconds
    if ($remaining -gt 0) { Start-Sleep -Milliseconds $remaining }
}
function Get-Sha256([string]$Path, [long]$TotalBytes = 0, [long]$OffsetBytes = 0, [string]$Phase = 'verifying') {
    $fileStream = [IO.File]::OpenRead($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    $buffer = New-Object byte[] (1024 * 1024)
    [long]$read = 0
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $pace = [bool](Request-Value 'gentle' $false)
    try {
        while ($true) {
            $blockTimer = [Diagnostics.Stopwatch]::StartNew()
            $count = $fileStream.Read($buffer, 0, $buffer.Length)
            if ($count -le 0) { break }
            [void]$hash.TransformBlock($buffer, 0, $count, $buffer, 0)
            $read += $count
            Wait-Gentle $pace $blockTimer.ElapsedMilliseconds
            if ($TotalBytes -gt 0 -and ($timer.ElapsedMilliseconds -ge 250 -or $read -eq $fileStream.Length)) {
                Write-ProgressJson $Phase ([Math]::Round(100.0 * ($OffsetBytes + $read) / $TotalBytes, 1)) ('Reading ' + [IO.Path]::GetFileName($Path))
                $timer.Restart()
            }
        }
        [void]$hash.TransformFinalBlock($buffer, 0, 0)
        return [BitConverter]::ToString($hash.Hash).Replace('-', '').ToLowerInvariant()
    }
    finally { $hash.Dispose(); $fileStream.Dispose() }
}
function Get-Manifest([string]$Source) {
    $root = [IO.Path]::GetFullPath($Source).TrimEnd('\') + '\'
    if (-not [IO.Directory]::Exists($root)) { throw 'The source folder does not exist.' }
    $items = @(Get-ChildItem -LiteralPath $root -Recurse -Force -ErrorAction Stop)
    if (@($items | Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 }).Count -gt 0) {
        throw 'Source folders cannot contain symbolic links or junctions.'
    }
    $files = @($items | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName)
    if ($files.Count -eq 0) { throw 'The source folder contains no files.' }
    $manifest = @(foreach ($file in $files) {
        [pscustomobject]@{ path = $file.FullName.Substring($root.Length); bytes = [long]$file.Length; sha256 = (Get-Sha256 $file.FullName) }
    })
    return ,$manifest
}
function Assert-ManifestUnchanged([string]$Source, $Manifest) {
    # Re-hash every source byte and compare with the pre-staging manifest. A change
    # that keeps the same size and timestamps must still fail closed before writing.
    $current = Get-Manifest $Source
    if (($current | ConvertTo-Json -Compress) -ne ($Manifest | ConvertTo-Json -Compress)) { throw 'Source files changed while the image was prepared. Nothing was burned.' }
}
function Build-Image([string]$Source, [string]$Label, [bool]$DvdVideo, $Recorder = $null) {
    if ([string]::IsNullOrWhiteSpace($Label) -or $Label.Length -gt 32 -or $Label -match '[\x00-\x1F\\/]') { throw 'Disc labels must contain 1 to 32 characters without slashes or control characters.' }
    if ($DvdVideo -and -not [IO.File]::Exists((Join-Path $Source 'VIDEO_TS\VIDEO_TS.IFO'))) { throw 'DVD-Video requires an authored VIDEO_TS folder with VIDEO_TS.IFO.' }
    $image = New-Com 'IMAPI2FS.MsftFileSystemImage'
    if ($null -eq $Recorder) { $image.ChooseImageDefaultsForMediaType(9) } else { $image.ChooseImageDefaults($Recorder) }
    $image.FileSystemsToCreate = $(if ($DvdVideo) { 5 } else { 7 })
    $image.UDFRevision = 0x102
    $image.VolumeName = $Label
    # Keep staging on the job's checked output volume, rather than the system TEMP drive.
    $workingDirectory = [IO.Path]::GetDirectoryName($Source.TrimEnd('\'))
    if ($workingDirectory -and [IO.Directory]::Exists($workingDirectory)) { $image.WorkingDirectory = $workingDirectory }
    $image.StageFiles = $true
    $root = Track-Com $image.Root
    [void]$root.AddTree($Source, $false)
    $result = Track-Com $image.CreateResultImage()
    $stream = Track-Com $result.ImageStream
    return @{ image = $image; result = $result; stream = $stream; blocks = [long]$result.TotalBlocks; bytes = [long]$result.TotalBlocks * [long]$result.BlockSize }
}
function Get-Inspection {
    $drives = @()
    $volumes = @()
    $events = @()
    $inspectionOk = $true
    try {
        $master = New-Com 'IMAPI2.MsftDiscMaster2'
        for ($index = 0; $index -lt $master.Count; $index++) {
            $id = [string]$master.Item($index)
            $drive = [ordered]@{ id = $id; name = 'Optical recorder'; letter = $null; can_write = $false; supports_dvd = $false; media_type = 'Unknown'; physical_media_type = 0; blank = $false; writable = $false; capacity_bytes = 0; free_bytes = 0; write_speeds = @(); write_protected = $false; media_status = 0; error = $null }
            try {
                $recorder = New-Recorder $id
                $drive.name = (([string]$recorder.VendorId).Trim() + ' ' + ([string]$recorder.ProductId).Trim()).Trim()
                $drive.letter = Recorder-Letter $recorder
                $writer = New-Writer $recorder
                $drive.can_write = [bool]$writer.IsRecorderSupported($recorder)
                $drive.supports_dvd = @($recorder.SupportedProfiles | Where-Object { [int]$_ -in @(17, 18, 19, 20, 21, 22, 26, 27, 42, 43) }).Count -gt 0
                $drive.physical_media_type = [int]$writer.CurrentPhysicalMediaType
                $mediaNames = @{ 0 = 'No media / unknown'; 1 = 'CD-ROM'; 2 = 'CD-R'; 3 = 'CD-RW'; 4 = 'DVD-ROM'; 5 = 'DVD-RAM'; 6 = 'DVD+R'; 7 = 'DVD+RW'; 8 = 'DVD+R DL'; 9 = 'DVD-R'; 10 = 'DVD-RW'; 11 = 'DVD-R DL'; 12 = 'Disk'; 13 = 'DVD+RW DL'; 14 = 'HD DVD-ROM'; 15 = 'HD DVD-R'; 16 = 'HD DVD-RAM'; 17 = 'BD-ROM'; 18 = 'BD-R'; 19 = 'BD-RE' }
                $drive.media_type = $mediaNames[$drive.physical_media_type]
                $drive.media_status = [int]$writer.CurrentMediaStatus
                $drive.blank = ($drive.media_status -band 2) -ne 0 -and [bool]$writer.MediaHeuristicallyBlank
                $drive.write_protected = ($drive.media_status -band 0x2000) -ne 0 -or [int]$writer.WriteProtectStatus -ne 0
                $drive.capacity_bytes = [long]$writer.TotalSectorsOnMedia * 2048
                $drive.free_bytes = [long]$writer.FreeSectorsOnMedia * 2048
                $drive.write_speeds = @($writer.SupportedWriteSpeeds | ForEach-Object { [int]$_ })
                $drive.writable = $drive.can_write -and $writer.IsCurrentMediaSupported($recorder) -and $drive.blank -and -not $drive.write_protected -and ($drive.media_status -band 0xFC00) -eq 0
            } catch { $drive.error = $_.Exception.Message }
            $drives += $drive
        }
    } catch { $inspectionOk = $false; $events += @{ provider = 'LumaBurn'; message = $_.Exception.Message; level = 'Error' } }
    try {
        $volumes = @(Get-CimInstance Win32_LogicalDisk -OperationTimeoutSec 5 | ForEach-Object { @{ letter = $_.DeviceID; label = $_.VolumeName; filesystem = $_.FileSystem; free_bytes = $_.FreeSpace; capacity_bytes = $_.Size; drive_type = [int]$_.DriveType; is_usb = [int]$_.DriveType -eq 2; is_system = $_.DeviceID -eq $env:SystemDrive; read_only = [int]$_.DriveType -notin @(2, 3) } })
    } catch { $events += @{ provider = 'LumaBurn'; message = ('Volume inspection: ' + $_.Exception.Message); level = 'Warning' } }
    try {
        # USB enclosures can report a fixed logical drive. Match partitions to the physical bus.
        $storageDisks = @{}
        Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Disk -OperationTimeoutSec 5 | ForEach-Object { $storageDisks[[int]$_.Number] = $_ }
        $partitions = @(Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_Partition -OperationTimeoutSec 5)
        foreach ($partition in $partitions) {
            $letter = [string]$partition.DriveLetter
            if ($letter -notmatch '^[A-Za-z]$') { continue }
            $disk = $storageDisks[[int]$partition.DiskNumber]
            if ($null -eq $disk) { continue }
            foreach ($volume in $volumes) {
                if ($volume.letter -eq ($letter + ':')) {
                    $volume.is_usb = [int]$disk.BusType -eq 7
                    $volume.is_system = [bool]$disk.IsSystem -or [bool]$disk.IsBoot -or $volume.is_system
                    $volume.read_only = [bool]$disk.IsReadOnly -or [bool]$partition.IsReadOnly -or $volume.read_only
                }
            }
        }
    } catch { $events += @{ provider = 'LumaBurn'; message = ('USB bus inspection: ' + $_.Exception.Message); level = 'Warning' } }
    try {
        $recent = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = @('cdrom', 'disk', 'storahci'); StartTime = (Get-Date).AddDays(-1); Level = @(1, 2, 3) } -MaxEvents 12 -ErrorAction Stop
        foreach ($event in $recent) { $events += @{ provider = $event.ProviderName; id = $event.Id; timestamp = $event.TimeCreated.ToUniversalTime().ToString('o'); level = $event.LevelDisplayName; message = $event.Message } }
    } catch {
        if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { $events += @{ provider = 'LumaBurn'; level = 'Warning'; message = ('Event-log inspection: ' + $_.Exception.Message) } }
    }
    return @{ ok = $inspectionOk; action = 'inspect'; drives = $drives; volumes = $volumes; events = $events }
}
function Inspect-WithTimeout {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $PSHOME 'powershell.exe'
    $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Action inspect -RequestPath "' + [IO.Path]::GetFullPath($RequestPath) + '"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $info.EnvironmentVariables['LUMABURN_INSPECT_WORKER'] = '1'
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) {
            $process.Kill()
            return @{ ok = $false; action = 'inspect'; error = 'Drive inspection timed out after 15 seconds. Close other disc applications and refresh.'; drives = @(); volumes = @(); events = @(@{ provider = 'LumaBurn'; level = 'Warning'; message = 'Optical inspection exceeded its time limit.' }) }
        }
        $process.WaitForExit()
        $output = $stdout.Result.Trim()
        if ([string]::IsNullOrWhiteSpace($output)) { throw ('Drive inspection returned no result. ' + $stderr.Result.Trim()) }
        return ($output -split '[\r\n]+' | Select-Object -Last 1 | ConvertFrom-Json)
    } finally { $process.Dispose() }
}
function Verify-Files([string]$Source, [string]$DiscPath, $Manifest = $null, [int]$WaitSeconds = 0) {
    $sourceRoot = [IO.Path]::GetFullPath($Source).TrimEnd('\') + '\'
    $discRoot = [IO.Path]::GetFullPath($DiscPath).TrimEnd('\') + '\'
    if ($sourceRoot.Equals($discRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Verification requires a separate disc or mounted image path.' }
    if ($null -eq $Manifest) { $Manifest = Get-Manifest $Source }
    $deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
    do {
        $ready = [IO.Directory]::Exists($discRoot) -and [IO.File]::Exists((Join-Path $discRoot $Manifest[0].path))
        if ($ready -or [DateTime]::UtcNow -ge $deadline) { break }
        Start-Sleep -Milliseconds 1000
    } while ($true)
    $files = @()
    $errors = @()
    [long]$bytesVerified = 0
    [long]$totalBytes = ($Manifest | Measure-Object -Property bytes -Sum).Sum
    [int]$matched = 0
    foreach ($entry in $Manifest) {
        $file = [ordered]@{ path = $entry.path; bytes = $entry.bytes; source_sha256 = $entry.sha256; disc_sha256 = $null; matched = $false; error = $null }
        try {
            $target = Join-Path $discRoot $entry.path
            if (-not [IO.File]::Exists($target)) { throw 'File is missing from the disc, or Windows has not mounted the disc.' }
            $file.disc_sha256 = (Get-Sha256 $target $totalBytes $bytesVerified)
            $actualBytes = [long](Get-Item -LiteralPath $target).Length
            $bytesVerified += $actualBytes
            $file.matched = $file.disc_sha256 -eq $entry.sha256 -and $actualBytes -eq $entry.bytes
            if (-not $file.matched) { throw 'SHA-256 or file length differs from the source.' }
            $matched++
        } catch { $file.error = $_.Exception.Message; $errors += @{ path = $entry.path; message = $file.error } }
        $files += $file
        Write-ProgressJson 'verifying' $(if ($totalBytes -gt 0) { [Math]::Round(100.0 * $bytesVerified / $totalBytes, 1) } else { 100 }) ('Read back ' + $entry.path)
    }
    try {
        $expected = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        foreach ($entry in $Manifest) { [void]$expected.Add($entry.path) }
        foreach ($actual in @(Get-ChildItem -LiteralPath $discRoot -File -Recurse -Force -ErrorAction Stop)) {
            $relative = $actual.FullName.Substring($discRoot.Length)
            if (-not $expected.Contains($relative)) { $errors += @{ path = $relative; message = 'Unexpected file on the disc.' } }
        }
    } catch { $errors += @{ path = $discRoot; message = $_.Exception.Message } }
    return @{ ok = $errors.Count -eq 0; action = 'verify'; verified = $errors.Count -eq 0; algorithm = 'SHA-256'; disc_path = $discRoot; files_expected = $Manifest.Count; files_verified = $matched; bytes_verified = $bytesVerified; files = $files; errors = $errors }
}
function Load-Native {
    if ('LumaBurn.Native' -as [type]) { return }
    Add-Type -TypeDefinition @"
using System;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

namespace LumaBurn {
    [ComImport, Guid("D2FFD834-958B-426D-8470-2A13879C6A91"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IBurnVerification {
        void SetLevel(int value);
        void GetLevel(out int value);
    }
    [ComVisible(true), Guid("2735413C-7F64-5B0F-8F00-5D77AFBE261E"), InterfaceType(ComInterfaceType.InterfaceIsIDispatch)]
    public interface IWriteEvents {
        [DispId(0x200)] void Update([MarshalAs(UnmanagedType.IDispatch)] object sender, [MarshalAs(UnmanagedType.IDispatch)] object progress);
    }
    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    public sealed class BurnEvents : IWriteEvents, IDisposable {
        IConnectionPoint connection;
        int cookie;
        long lastTick;
        public BurnEvents(object writer) {
            Guid guid = typeof(IWriteEvents).GUID;
            ((IConnectionPointContainer)writer).FindConnectionPoint(ref guid, out connection);
            connection.Advise(this, out cookie);
        }
        static long Property(object item, string name) {
            return Convert.ToInt64(item.GetType().InvokeMember(name, BindingFlags.GetProperty, null, item, null));
        }
        public void Update(object sender, object progress) {
            try {
                if (DateTime.UtcNow.Ticks - lastTick < TimeSpan.TicksPerMillisecond * 500) return;
                lastTick = DateTime.UtcNow.Ticks;
                long action = Property(progress, "CurrentAction");
                string[] phases = {"validating", "formatting", "initializing", "calibrating", "writing", "finalizing", "finished", "verifying"};
                string phase = action >= 0 && action < phases.Length ? phases[action] : "writing";
                string percent = "null";
                if (action == 4) {
                    long total = Property(progress, "SectorCount");
                    long written = Property(progress, "LastWrittenLba") - Property(progress, "StartLba") + 1;
                    if (total > 0) percent = Math.Max(0, Math.Min(100, 100.0 * written / total)).ToString("0.0", CultureInfo.InvariantCulture);
                }
                Console.Out.WriteLine("{\"phase\":\"" + phase + "\",\"progress\":" + percent + ",\"message\":\"Disc " + phase + "\"}");
            } catch { /* Optional progress must never interrupt a burn. */ }
        }
        public void Dispose() {
            if (connection == null) return;
            try { connection.Unadvise(cookie); } finally { Marshal.ReleaseComObject(connection); connection = null; }
        }
    }
    public static class Native {
        [DllImport("kernel32.dll")] static extern uint SetThreadExecutionState(uint flags);
        public static void KeepAwake(bool awake) { SetThreadExecutionState(awake ? 0x80000001u : 0x80000000u); }
        public static bool FullVerification(object writer) {
            try { ((IBurnVerification)writer).SetLevel(2); return true; }
            catch (InvalidCastException) { return false; }
            catch (COMException exception) {
                if (exception.ErrorCode == unchecked((int)0x80004002) || exception.ErrorCode == unchecked((int)0x80004001)) return false;
                throw;
            }
        }
        public static long SaveStream(object value, string path, long expected, bool gentle) {
            IStream stream = (IStream)value;
            IntPtr readCount = Marshal.AllocCoTaskMem(sizeof(int));
            byte[] buffer = new byte[1024 * 1024];
            long total = 0;
            long lastTick = 0;
            System.Diagnostics.Stopwatch blockTimer = new System.Diagnostics.Stopwatch();
            try {
                stream.Seek(0, 0, IntPtr.Zero);
                using (FileStream output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                    for (;;) {
                        blockTimer.Restart();
                        stream.Read(buffer, buffer.Length, readCount);
                        int count = Marshal.ReadInt32(readCount);
                        if (count == 0) break;
                        output.Write(buffer, 0, count);
                        total += count;
                        if (gentle) {
                            int remaining = 16 - (int)blockTimer.ElapsedMilliseconds;
                            if (remaining > 0) System.Threading.Thread.Sleep(remaining);
                        }
                        if (expected > 0 && (DateTime.UtcNow.Ticks - lastTick >= TimeSpan.TicksPerMillisecond * 250 || total >= expected)) {
                            lastTick = DateTime.UtcNow.Ticks;
                            string percent = Math.Min(100, 100.0 * total / expected).ToString("0.0", CultureInfo.InvariantCulture);
                            Console.Out.WriteLine("{\"phase\":\"imaging\",\"progress\":" + percent + ",\"message\":\"Saving ISO image\"}");
                        }
                    }
                    output.Flush(true);
                }
                return total;
            } finally { Marshal.FreeCoTaskMem(readCount); }
        }
    }
}
"@
}

$recorder = $null
$writer = $null
$eventSink = $null
$exclusive = $false
$mcnDisabled = $false
$awake = $false
$partialOutput = $null
try {
    $script:Request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $script:Request -or $script:Request -is [array] -or $script:Request -is [string]) { throw 'The request must be a JSON object.' }
    if ($Action -eq 'inspect') {
        if ($env:LUMABURN_INSPECT_WORKER -eq '1') { $result = Get-Inspection } else { $result = Inspect-WithTimeout }
    } else {
        $source = [string](Request-Value 'source_dir')
        if ([string]::IsNullOrWhiteSpace($source)) { throw 'A source folder is required.' }
        $source = [IO.Path]::GetFullPath($source)
        if ($Action -eq 'verify') {
            $discPath = [string](Request-Value 'disc_path')
            if ([string]::IsNullOrWhiteSpace($discPath)) {
                $recorder = New-Recorder ([string](Request-Value 'recorder_id'))
                $letter = Recorder-Letter $recorder
                if (-not $letter) { throw 'The selected recorder has no drive letter for read-back verification.' }
                $discPath = $letter + '\'
            }
            $result = Verify-Files $source $discPath
        } else {
            $label = [string](Request-Value 'label' 'LUMABURN')
            $dvdVideo = [bool](Request-Value 'dvd_video' $false)
            $verify = [bool](Request-Value 'verify' $true)
            $manifest = Get-Manifest $source
            if ($Action -eq 'burn') {
                $recorder = New-Recorder ([string](Request-Value 'recorder_id'))
                $writer = New-Writer $recorder
                [void](Assert-BlankMedia $writer $recorder 0 $dvdVideo)
                $letter = Recorder-Letter $recorder
                if ($verify -and -not $letter) { throw 'The recorder has no mounted drive letter for read-back verification.' }
            }
            Write-ProgressJson 'preparing' $null 'Building the disc filesystem and staging source files'
            $built = Build-Image $source $label $dvdVideo $recorder
            Assert-ManifestUnchanged $source $manifest
            Load-Native
            if ($Action -eq 'image') {
                $output = [string](Request-Value 'output_path')
                if ([string]::IsNullOrWhiteSpace($output)) { throw 'An ISO output path is required.' }
                $output = [IO.Path]::GetFullPath($output)
                if ($output.StartsWith($source.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Save the ISO outside the source folder.' }
                if ([IO.File]::Exists($output)) { throw 'The ISO output file already exists. Choose a new filename.' }
                if (-not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($output))) { throw 'The ISO output folder does not exist.' }
                $partialOutput = $output + '.' + [Guid]::NewGuid().ToString('N') + '.partial'
                $saved = [LumaBurn.Native]::SaveStream($built.stream, $partialOutput, $built.bytes, [bool](Request-Value 'gentle' $false))
                if ($saved -ne $built.bytes) { throw 'The ISO stream ended before the complete image was saved.' }
                [IO.File]::Move($partialOutput, $output)
                $partialOutput = $null
                $result = @{ ok = $true; action = 'image'; output_path = $output; bytes = $saved; blocks = $built.blocks; label = $label; dvd_video = $dvdVideo; files = $manifest.Count; sha256 = (Get-Sha256 $output $saved 0 'hashing') }
            } else {
                $writer.ForceOverwrite = $false
                $writer.ForceMediaToBeClosed = $true
                $speed = [int](Request-Value 'speed' -1)
                if ($speed -ne -1 -and $speed -notin @($writer.SupportedWriteSpeeds)) { throw 'The requested speed is not supported by the inserted disc. Refresh the drive.' }
                $writer.SetWriteSpeed($speed, $false)
                $fullVerification = $false
                if ($verify) { $fullVerification = [LumaBurn.Native]::FullVerification($writer) }
                $recorder.AcquireExclusiveAccess($false, 'LumaBurn')
                $exclusive = $true
                $recorder.DisableMcn()
                $mcnDisabled = $true
                [void](Assert-BlankMedia $writer $recorder $built.blocks $dvdVideo)
                $eventSink = New-Object LumaBurn.BurnEvents($writer)
                [LumaBurn.Native]::KeepAwake($true)
                $awake = $true
                Write-ProgressJson 'writing' 0 'Writing and finalizing the blank disc'
                $script:WriteStarted = $true
                [void]$writer.Write($built.stream)
                $script:Burned = $true
                $eventSink.Dispose()
                $eventSink = $null
                $recorder.ReleaseExclusiveAccess()
                $exclusive = $false
                $recorder.EnableMcn()
                $mcnDisabled = $false
                $result = @{ ok = $true; action = 'burn'; burned = $true; finalized = $true; label = $label; image_bytes = $built.bytes; imapi_full_verification = $fullVerification; verification = $null }
                if ($verify) {
                    Write-ProgressJson 'verifying' 0 'Waiting for Windows to mount the finished disc, then reading every file'
                    $verification = Verify-Files $source ($letter + '\') $manifest 90
                    $result.verification = $verification
                    $result.ok = $verification.ok
                    if (-not $verification.ok) { $result.error = 'The disc was burned, but file read-back verification failed. Inspect the file errors before using it.' }
                }
            }
        }
    }
    Write-Json $result
    if (-not $result.ok) { exit 1 }
} catch {
    Write-Json @{ ok = $false; action = $Action; error = $_.Exception.Message; burned = $script:Burned; write_started = $script:WriteStarted; media_may_be_changed = $script:WriteStarted; hresult = ('0x{0:X8}' -f $_.Exception.HResult) }
    exit 1
} finally {
    if ($null -ne $eventSink) { try { $eventSink.Dispose() } catch { } }
    if ($exclusive) { try { $recorder.ReleaseExclusiveAccess() } catch { } }
    if ($mcnDisabled) { try { $recorder.EnableMcn() } catch { } }
    if ($awake) { [LumaBurn.Native]::KeepAwake($false) }
    if ($null -ne $partialOutput -and [IO.File]::Exists($partialOutput)) { [IO.File]::Delete($partialOutput) }
    Release-Com
}
