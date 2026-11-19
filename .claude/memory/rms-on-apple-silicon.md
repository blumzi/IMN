---
name: rms-on-apple-silicon
description: Two Mac-only RMS breakages that stop Utils.FRbinViewer running on this machine
metadata: 
  node_type: memory
  type: project
  originSessionId: 6b51fe97-05d2-4839-bcea-0637a2eb9c78
  modified: 2026-09-09T11:00:21.387Z
---

Two RMS failures that happen ONLY on the Apple Silicon Mac, not on the
station. Both were diagnosed 2026-09-09 while trying to render a bolide
clip locally. Neither is an IMN bug and neither may be fixed in RMS
(see [[rms-is-vendored]]).

1. **Cython build fails.** RMS `choosePlatform()`
   (RMS/RMS/ConfigReader.py, `if 'arm' in os.uname()[4]`) sees the Mac's
   `arm64` and concludes it is a Raspberry Pi, so it picks `rpi_weave`
   from `.config`: `-O3 -mfpu=neon -funsafe-loop-optimizations
   -ftree-loop-if-convert-stores`. Apple clang hard-errors on
   `-mfpu=` and `-ftree-loop-if-convert-stores`. Anything that triggers
   pyximport dies, e.g. `RMS.Astrometry.CyFunctions`.
   `Utils.FRbinViewer` imports `Utils.ShowerAssociation` and
   `RMS.Astrometry.Conversions` at MODULE TOP LEVEL, so it cannot be
   imported at all here -- no flag combination avoids it.

2. **`RMS.Formats.FRbin.read` fails under NumPy 2.** It does
   `int(np.fromfile(fid, dtype=np.uint32, count=1))`, which NumPy 2
   refuses ("only 0-dimensional arrays can be converted to Python
   scalars"). Fine on the station's NumPy 1.x, broken with the local
   venv's NumPy 2.5 (see [[local-test-venv]]).

Consequence for IMN: `bolides.py` keeps FRbinViewer as the PREFERRED
renderer (it is what the station runs, and it burns in frame number,
timestamp and shower name) but falls back to compositing the FR crops
itself when FRbinViewer produces nothing. The fallback parses the FR
binary with IMN's own `_read_fr()` rather than `RMS.Formats.FRbin`, to
dodge problem 2. FR layout: uint32 line count, per line a uint32 frame
count, per frame yc/xc/t/size as uint32 then size*size uint8 pixels.

Fallback clips have NO overlays. Untried alternative if overlays are
ever needed locally: `python setup.py build_ext --inplace` in ~/src/RMS
(its setup.py uses clang-safe `-O3 -Wall`), which would precompile the
extensions so pyximport never runs.

See [[bolide-video-publishing]].
