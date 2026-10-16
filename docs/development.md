# Development and verification

LumaBurn is a local Windows app: a Python standard-library server packaged as
`lumaburn/`, browser JavaScript in `app/`, and PowerShell helpers in `native/`.
There are no Python or npm runtime dependencies. Node.js is only used for the
JavaScript checks.

## Layout

| Path | Contents |
| --- | --- |
| `lumaburn/` | Application package, including automatic tool setup in `setup.py`. |
| `app/` | Browser interface (`index.html`, `app.js`, `style.css`). |
| `native/` | Windows PowerShell helpers for file pickers and IMAPI disc operations. |
| `docs/` | Reference documentation. |
| `tests/` | Python and Node regression checks. |

## Checks

Run from the repository root:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
node tests/test_devices.js
node tests/test_burn_workflow.js
python tests/smoke.py
node --check app/app.js
git diff --check
```

The unit and Node checks use temporary directories and mocks and never write to a
physical disc. `tests/smoke.py` needs FFmpeg, FFprobe, and dvdauthor; it creates
disposable synthetic media under ignored `.local/`, converts NTSC/PAL video DVDs,
MP4, and MP3, and verifies exact file copies. It does not write to a disc.

Native optical support is Windows-only and requires IMAPI2; it has not been
validated on Linux or macOS. Physical DVD-player compatibility is a manual check.

## Notes

- Keep generated media, logs, downloaded tools, and local evidence under ignored
  `.local/` or `tools/`.
- Do not commit drive identifiers, machine-specific paths, or user media.
- Startup runs `lumaburn/setup.py` in a background thread. It reuses detected
  tools, then downloads any missing hash-pinned packages into `.local/dependencies/`
  and extracts them into ignored `tools/`. `GET /api/state` includes setup status;
  token-protected `POST /api/setup` retries failed setup while the app is idle.
- Automatic FFmpeg setup uses Gyan 9.0.2 essentials (linked from FFmpeg's download
  page). DVD authoring retains the pinned DVDStyler 3.2.1 and innoextract 1.9
  packages. Keep download URLs and SHA-256 hashes together when updating them.
- Managed tool folders become visible to detection only after a `.ready` marker
  is written. Failed downloads or extraction leave setup retryable.
