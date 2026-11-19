---
name: station-only-render-bugs
description: "Three rendering bugs that appear only on the Pis, found 2026-09-11 on il0003"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6b51fe97-05d2-4839-bcea-0637a2eb9c78
  modified: 2026-09-11T08:32:20.872Z
---

Three failures that only show up on a station, never on a dev Mac. All found
2026-09-11 by running the pipeline on il0003 against a real night, all fixed
in PR #11. None of them raised an error — each failed silently.

1. **RMS cannot save the track stack it computes.** `Utils/TrackStack` hands
   a correct array to matplotlib and `savefig` writes a uniformly WHITE JPEG.
   Measured: array mean 49.74 / std 20.84; saved file mean 255.00 / std 0.00;
   `cv2.imwrite` of that same array is perfect. Backend agg, Python 3.7,
   matplotlib as pinned on the stations. It renders fine at 1772x1059 and
   blank at 865x432 — the reason was never established, and does not matter:
   `trackstack_runner.py` now intercepts `plt.imshow` and writes the array
   with OpenCV, drawing the caption itself (textoption=2 is matplotlib-drawn
   and would be lost too). COUPLING: this relies on trackStack() calling
   plt.imshow() once with the finished 2D array.

2. **TrackStack's `-s/--showers` filter is unusable.** `shouldInclude()` does
   `associations[(ff_name, 1.0)]` — meteor number hardcoded to 1 — so an FF
   whose shower meteor is not the FIRST detection is dropped, and a bare
   `except` turns every miss into a quiet False. Fix: associate ourselves
   (`analyse_night`) and stage just that shower's FFs into a directory that
   TrackStack consumes UNFILTERED.

3. **Station ffmpeg is 4.1.4, and `tpad` needs 4.2.** Bolide retiming failed
   on every clip with "No such filter: 'tpad'" and published at original
   speed. Replaced with the `loop` filter (much older): repeat the last frame
   N times, then stretch with setpts.

TIMINGS on il0003 (70 FF night): a full unfiltered stack takes ~17 min; a
12-FF per-shower stack ~161 s. The 2 hour timeout is far more generous than
needed. Note a FAST run that produces nothing is the signature of a stack
that plotted no frames.

Lesson worth keeping: verify renderer/tool capabilities ON THE STATION, not
on the Mac. The Mac has ffmpeg 9 and a working matplotlib; the Pi has neither.

See [[bolide-video-publishing]] and [[rms-is-vendored]].
