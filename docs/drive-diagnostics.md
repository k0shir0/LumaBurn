# Drive diagnostics

Open **Devices** to check detected optical writers, blank-media status, capacity,
write protection, supported speeds, and recent storage warnings. The app refreshes
devices while idle and pauses background inspection during jobs and disc tests.

## A writer or disc is unavailable

- Check that Windows sees the drive and that its cable or enclosure is connected.
- A reader cannot burn. Select a compatible writer and media type.
- Use a blank, supported, unprotected disc. LumaBurn never erases an existing disc
  or appends a session to it. An optical drive without writable media can still
  appear in Devices.
- Close other programs using the writer. Recorder-access failures can mean
  another process holds the drive.
- Use **Save ISO only** to prepare an image while a writer is unavailable.

## Preparation or writing is slow

**Full speed** allows more conversion resources. **Gentle** trades preparation
speed for lower resource use. Two-pass DVD conversion can take longer than the
disc write. Choose **Add files** rather than drag and drop to avoid an extra
staging copy of large inputs, and use storage with enough free working space.

LumaBurn requests the fastest speed supported by the inserted medium. The drive
may choose a different supported speed. Firmware, media, USB connections, source
storage, and competing activity can limit throughput. Background device scans
pause while a job runs.

## Read-back fails after writing

A completed write can still fail verification if Windows cannot mount the disc,
a file is missing or unreadable, or its size or SHA-256 differs. Check the error
on **Activity** and the mounted filesystem before relying on the disc. Keep the
original files. A failed write can leave a partially written disc.

**Tests** checks Windows visibility, directory readability, DVD navigation-file
signatures, IFO/BUP backups, VOB presence, and the first VOB's format per title
set. It is not a full-sector scan or a standalone-player playback test. A clean
Windows status or a successful small sample cannot establish whole-drive health.

## References

- [Microsoft: write speed selection](https://learn.microsoft.com/en-us/windows/win32/api/imapi2/nf-imapi2-idiscformat2data-setwritespeed)
- [Microsoft: media state flags](https://learn.microsoft.com/en-us/windows/win32/api/imapi2/ne-imapi2-imapi_format2_data_media_state)
- [Microsoft: burn verification levels](https://learn.microsoft.com/en-us/windows/win32/api/imapi2/ne-imapi2-imapi_burn_verification_level)
