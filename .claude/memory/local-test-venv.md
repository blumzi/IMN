---
name: local-test-venv
description: Local Mac .venv for testing the bolide pipeline against station data
metadata: 
  node_type: memory
  type: project
  originSessionId: 7bf81146-7be3-4447-bfbe-f7f5ba604e20
  modified: 2026-09-09T11:00:56.225Z
---

To test the bolide pipeline on this Mac (no RMS running), `~/src/IMN/.venv`
(Python 3.13) is set up so `import RMS.*` works WITHOUT compiling RMS's
Cython extensions:

- RMS repo root is on the venv path via a `.pth`:
  `.venv/lib/python3.13/site-packages/rms.pth` -> `/Users/blumzi/src/RMS`
  (our code path only touches pure-Python parsers, so no build needed).
- Deps installed (pure wheels): numpy(2.5.x), gitpython, matplotlib,
  astropy, scipy, Pillow, pyyaml, ephem, opencv-python-headless, plus the
  upload libs google-api-python-client / google-auth / google-auth-oauthlib.
- `RMS.Misc` imports tkinter at module top, so `brew install python-tk@3.13`
  was required (Homebrew python ships without _tkinter).
- `brew install ffmpeg` (FRbinViewer needs it to emit mp4).

Real station data to test against: `~/data/IL0003/20260818` (a full night
from IL0003 — 27 FR, 32 FF, 3 FTPdetectinfo). Three bolides pass the
mag<=-1 threshold; one of them has no matching FR file, so only two render.

Run selection only:  `.venv/bin/python bolides.py <archived_night_dir> --dry-run`
Render, no publish:  `.venv/bin/python bolides.py <night> --no-upload -c ~/src/RMS`
Full render+publish: `.venv/bin/python bolides.py <night> -c ~/src/RMS`
CREDENTIALS ARE LIVE as of 2026-09-09 — a run without `--dry-run` or
`--no-upload` really does publish to the IAA channel. Note ~/src/RMS/.config
is the stock RMS one (stationID XX0001); harmless now that the station code
is taken from the data, but it logs a mismatch warning.

Verified end-to-end against a hand-crafted 12-column FTPdetectinfo through
the real RMS reader: threshold exclusion, sat-fraction ranking, and real
great-circle trail length all correct. NOTE: RMS.Formats.writeFTPdetectinfo's
simple API only emits 8 columns (no Mag/NSatPx) -- to test the saturation
metric you must hand-craft a 12-column file (header is 11 lines, then per
meteor: separator, ff_name, "Uncalibrated", params line, 12-col segments).

None of this .venv is committed (it's under ~/src/IMN but venvs aren't
tracked). See [[bolide-video-publishing]] and [[rms-is-vendored]].
