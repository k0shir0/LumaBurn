<h1 align="center">LumaBurn</h1>

Create video DVDs, burn data discs, convert video and audio, and copy files to USB
storage. LumaBurn runs locally on Windows.

## Requirements

- Windows 10 or 11 and [Python 3.11+](https://www.python.org/downloads/windows/).
  Enable **Add Python to PATH** when installing Python.
- [FFmpeg and FFprobe](https://ffmpeg.org/download.html) for conversion. Use a
  current Windows build, preferably FFmpeg 8 or newer, and add its `bin` folder
  to your PATH. Original-file copies and data discs do not need these tools.
- **dvdauthor** for DVD-player output. Install
  [DVDStyler](https://www.dvdstyler.org/en/downloads), or use the setup below.
- An optical **writer** and compatible blank media for burning. ISO export does
  not require a connected writer.

No account or additional Python packages are required.

## Setup and launch

Download or clone the project into a folder. To get the DVD authoring engine
without installing DVDStyler system-wide, run this from that folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Setup-tools.ps1
```

The setup downloads pinned upstream packages, verifies their SHA-256 hashes, and
extracts the tools locally. Install FFmpeg and FFprobe separately.

Double-click **Launch.cmd**, or run this from the project folder:

```powershell
python -m lumaburn
```

The app opens at <http://127.0.0.1:9847>. Closing its window leaves the server
running so an active burn can finish; use **Launch.cmd** to reopen it. When
started in a console, **Ctrl+C** waits for an active disc write before shutdown.

Check **Devices** for tool availability. Restart LumaBurn after installing tools
or changing PATH. Custom executable paths can be set with `LUMABURN_FFMPEG`,
`LUMABURN_FFPROBE`, and `LUMABURN_DVDAUTHOR`. A local `dvdauthor.exe` and its DLLs
can also go in `tools/`.

## Output formats

| Preset | Output | Intended use |
| --- | --- | --- |
| DVD player | MPEG-2 video, stereo AC-3, authored VIDEO_TS; NTSC or PAL | Standalone DVD players supporting the chosen standard and disc type |
| Data DVD | Original files, without recompression | Computers and devices that support those files |
| USB / TV | H.264/AAC MP4, up to 1080p; audio-only files become MP3 | Compatible TVs and media players |
| Original files | Exact copies with SHA-256 verification | Backup or transfer to a folder or USB drive |
| MP3 audio | Variable-bitrate MP3, including audio extracted from video | Music players |

DVD video is standard definition. LumaBurn plans its bitrate to fit the disc and
rejects videos that are too long. A 4.7 GB disc holds about 4.38 GiB.

## Use LumaBurn

1. On **Media**, choose **Add files**. Originals are read in place; drag and drop
   makes a local staging copy. Prefer **Add files** for large movies. Uncheck
   files to exclude them without removing them.
2. On **Output**, choose a format and destination. DVD player defaults to NTSC;
   select PAL when needed. Queue order is title order. DVDs play the titles in
   sequence without a visual menu.
3. Choose **Burn DVD** or **Burn disc** to write and finalize a blank disc, then
   verify every recorded file. **Save ISO only** creates an image and writes no
   disc. Other presets prepare or copy files to the chosen folder.
4. Follow progress and errors on **Activity**, then open the output folder.
   **Devices** shows hardware and tools. **Tests** checks a mounted disc or
   authored DVD folder. **Help** contains setup and usage instructions.

**Gentle** limits conversion threads and paces copying, hashing, and ISO saving
to keep resource use lower. **Full speed** removes that pacing and allows
automatic conversion threading. Writer speed still depends on the drive and
media. Only one preparation, estimate, or disc test runs at a time.

## Limitations and safeguards

- Burning accepts blank, supported, unprotected media. LumaBurn does not erase
  discs or format USB drives. Cancellation is disabled once native writing starts.
- Success requires writing, finalization, and SHA-256 read-back of every expected
  file. Disconnecting a writer during a burn can spoil the disc.
- Conversion keeps the first video and audio streams, with HDR tone-mapped to SDR.
  Extra audio tracks and subtitles are omitted. Keep originals to preserve them.
- H.264/H.265 files are not DVD-Video. Player codec, NTSC/PAL, disc-type, and USB
  filesystem support vary. FAT32 limits individual files to less than 4 GiB.
- **Tests** checks readability and DVD structure; it does not prove whole-disc
  health or playback in your player. See [drive diagnostics](docs/drive-diagnostics.md).
- Archive estimates on **Media** test bounded samples; they do not create an
  archive. Optional [7-Zip](https://www.7-zip.org/) or
  [WinRAR](https://www.rarlab.com/download.htm) can be used alongside built-in LZMA.

## Your data

Media processing stays on your computer. By default, staging, outputs, logs, and
diagnostics are under `.local/`; downloaded authoring tools are under `tools/`.
Choose another output folder when needed. Uploaded staging files remain until
you remove them after jobs finish. Original files are preserved.

LumaBurn listens on localhost and has no account or telemetry. Keep FFmpeg and
other tools current when opening untrusted media. Tool setup downloads from the
upstream projects linked above.

## License

LumaBurn's source uses the [MIT License](LICENSE).
[NOTICE.md](NOTICE.md) covers authorized media use, fair use, and external tools.
