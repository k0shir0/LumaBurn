<p align="center">
  <img src="docs/brand/lumaburn-logo.png" alt="LumaBurn" width="660">
</p>

Create video DVDs, burn data discs, convert video and audio, and copy files to USB
storage. LumaBurn runs locally on Windows.

## Requirements

- 64-bit Intel/AMD Windows 10 or 11 and [Python 3.11+](https://www.python.org/downloads/windows/).
  Enable **Add Python to PATH** when installing Python.
- An optical **writer** and compatible blank media for burning. ISO export does
  not require a connected writer.

No account or additional Python packages are required.

## Setup and launch

Download or clone the project into a folder and double-click **Launch.cmd**.

LumaBurn opens and automatically prepares any missing FFmpeg, FFprobe, and DVD
authoring tools. First launch needs an internet connection; progress appears at
the top of the app. If a download fails, select **Retry setup** there.
Existing tools are reused, and later launches work offline once setup finishes.
No setup commands, PATH edits, or administrator access are needed for these tools.

You can also start it with `python -m lumaburn` from the project folder.

The app opens at <http://127.0.0.1:9847>. Closing its window leaves the server
running so an active burn can finish; use **Launch.cmd** to reopen it. When
started in a console, **Ctrl+C** waits for an active disc write before shutdown.

Check **Devices** for tool availability. Setup verifies SHA-256 hashes before
extracting pinned upstream packages into `tools/`, without running an installer.
It uses FFmpeg's linked [Gyan Windows build](https://www.gyan.dev/ffmpeg/builds/)
and extracts the authoring engine from [DVDStyler](https://www.dvdstyler.org/).
Downloads are cached under `.local/dependencies/` for retries.

For an existing custom installation, `LUMABURN_FFMPEG`, `LUMABURN_FFPROBE`, and
`LUMABURN_DVDAUTHOR` override detection. Tools on PATH and an installed DVDStyler
are also detected. Restart after changing these paths.

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
