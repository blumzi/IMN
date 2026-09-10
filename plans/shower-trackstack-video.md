# Plan: Per-Station Cumulative Shower Track-Stack Videos

**Status:** Draft
**Date:** 2026-09-10
**Component:** IMN external script (runs on each IL00xx station after RMS uploads its nightly products)

## Goal

During an active meteor shower, each station maintains **one cumulative video per
shower** on the Israeli Astronomical Association's YouTube channel, showing that
station's track-stacked frames for every night of the shower so far. The video is
rebuilt and replaced each morning for as long as the shower is active.

## Scope

**In scope**
- Shower detection from the previous night's `FTPdetectinfo`
- Per-shower track-stack image generation via RMS `Utils/TrackStack`
- Per-station accumulation of stack images across the shower's activity window
- Daily rebuild of the cumulative video and replacement of the published one

**Out of scope**
- Multi-station composite stacks (would require a central collector; see Open Questions)
- Anything to do with the bolide video pipeline, which continues unchanged
- Publishing to astronomy.org.il (separate concern; link the playlist, not the video)

## Design decisions

### One video per station per shower, not one image per detection

Six stations against ~6 concurrently active showers in mid-December yields ~36 images
per night unthrottled — several hundred over a single shower. A cumulative video
collapses this to one artifact per station per shower.

### Cumulative, not nightly

Each rebuild covers the entire shower to date. Consequences:
- The newest upload is always the complete record, so deleting the previous one loses
  nothing.
- When the shower ends, the last video published is already the final summary. No
  separate "publish the season" step.

### "Replace" means insert-then-retire, one cycle late

**The YouTube Data API cannot replace a video's media.** `videos.update` changes
metadata only — title, description, privacy — and there is no endpoint to swap the
underlying file. Daily replacement is therefore: upload new, add to playlist, retire an
old one. The video URL changes every day.

Mitigation: publish and link the **playlist** URL, which is stable, never a video URL.

**Two generations are kept live, and the retired one is the day-before-yesterday's.**
A freshly uploaded video is not immediately playable — YouTube transcodes for minutes,
longer at 1080p. Retiring yesterday's in the same run leaves a window in which the
playlist holds one deleted video and one still processing, and a visitor sees nothing.
Keeping two generations costs one extra `videos.delete` per day and guarantees there is
always a finished video to watch.

The playlist therefore shows two entries for an active shower: today's (possibly still
processing) and yesterday's (certainly ready). Both are complete cumulative records, so
seeing either is fine — yesterday's is merely one night short.

### Stack images are archived outside `RMS_data`

RMS's disk-space management (`RMS/DeleteOldObservations.py`) reaps the oldest night
directories to stay within quota. It only walks `config.data_dir`, and selects
candidates via `getNightDirs()`, which matches the station-ID night-directory naming
convention. An archive at `~/imn/stacks/` is invisible to it.

Equally important: keeping the archive *outside* `RMS_data` means it does not count
toward `rms_data_quota`, so it cannot indirectly cause RMS to delete additional night
folders to compensate.

Once TrackStack has run and the JPEG is copied out, the source night folder is
disposable. The cumulative video rebuilds entirely from the archive.

### TrackStack runs once per night, never retroactively

TrackStack is the expensive step and it re-reads every FF file in the directory. It
runs on last night's archived directory only. Rebuilding N nights of history each
morning would grow without bound and is unnecessary — the JPEGs are already on disk.

## Pipeline

Runs from the existing IMN external script, after `ArchiveDetections` (which is what
produces `platepars_all_recalibrated.json`, required by TrackStack).

### 1. Determine active showers

**A shower is active if this station detected it last night.** Nothing is read from a
published calendar — no IMO working list, no hardcoded activity windows. The station's
own detections are the sole authority for what it makes a video of.

```python
from Utils.ShowerAssociation import showerAssociation

_, shower_counts = showerAssociation(config, [ftpdetectinfo_path],
                                     show_plot=False, save_plot=False)

active = [s.name for s, n in shower_counts if s is not None and n >= MIN_METEORS]
```

`shower_counts` is a ranked list of `(Shower, count)` pairs; `None` denotes sporadics
and must be filtered out.

- `MIN_METEORS` — default 4. Without a threshold, showers with a single marginal
  association generate their own video. At ZHR 3 a single camera will not clear this
  bar on most nights, which is the intent.

Deriving activity from detections rather than a calendar means the station is never
told a shower is running when it cannot see it. Clouded out, radiant below the horizon,
camera pointed elsewhere — in every case the shower is simply not active for that
station that night, with no special handling. It also removes the need for a
declination blacklist: a radiant that never rises from Israeli latitudes (PUP at
dec −45°) produces no associations, so it never clears `MIN_METEORS` in the first place.

Note this is a per-station, per-night judgement. Two stations can legitimately disagree
about whether a shower is active, and each is right about itself.

### 2. Generate the night's stack image

Run it as a **subprocess with a 2 hour timeout**, not as an in-process call:

```python
cmd = [sys.executable, "-m", "Utils.TrackStack", archived_dir,
       "-c", config.config_file_name,
       "-s", code,          # single shower code
       "-o", tmp_dir,
       "-t", "2",           # stationID, date, meteor count
       "-x",                # never try to open a window
       "--freecore"]

try:
    subprocess.run(cmd, cwd=config.rms_root_dir, timeout=TRACKSTACK_TIMEOUT)
except subprocess.TimeoutExpired:
    log.error("TrackStack for %s exceeded %ds; skipping this night", code,
              TRACKSTACK_TIMEOUT)
```

- `TRACKSTACK_TIMEOUT` — **7200 seconds**. TrackStack re-reads every FF file in the
  night, and this runs inside `rmsExternal`, which holds the reboot lock; RMS only
  ever *waits* on that lock and never clears it. An unbounded stack on a bad night
  therefore delays the station's reboot and can run into the next capture window. Two
  hours is generous for a normal night and still leaves daylight to spare.
- A subprocess is what makes the timeout enforceable at all — an in-process
  `trackStack()` call cannot be interrupted on a wall clock. It also contains a
  segfault or a memory blowout in a C extension, which would otherwise take the whole
  nightly hook down with it.
- The timeout is per shower. With several showers active, budget accordingly or take
  them in ranked order and stop when the night's total budget is spent.
- `-t 2` stamps station ID, date, and meteor count into the image, so the video
  self-labels.
- `--freecore` leaves a core available; the external script may run while uploads are
  still in flight.
- `-x` is mandatory, exactly as for FRbinViewer: without it the plotting backend tries
  to open a window and dies headless.
- The `-s` filter requires a config matching the data.
- Worth adding `nice`/`ionice` on top, so a long stack does not starve capture.
- **Measure one real run on a Pi before enabling this nightly.** The 2 hour bound is a
  safety net, not an estimate; if a typical night takes 40 minutes, six concurrent
  December showers will not fit and the ranked-order budget above becomes the real
  design.

### 3. Archive it

```
~/imn/stacks/<year>/<SHOWER>/<STATIONID>_<YYYYMMDD>_<SHOWER>.jpg
```

Example:

```
~/imn/stacks/2026/GEM/IL0001_20261204_GEM.jpg
              GEM/IL0001_20261205_GEM.jpg
```

Sizing: a stack JPEG is a few MB. A full Geminid season for one station is ~40 MB; a
year across all showers stays well under a gigabyte.

### 4. Rebuild the cumulative video

```bash
ffmpeg -framerate 1/3 -pattern_type glob \
  -i "$STACK_DIR/${STATION}_*_${SHOWER}.jpg" \
  -c:v libx264 -pix_fmt yuv420p -r 30 \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,\
pad=1920:1080:(ow-iw)/2:(oh-ih)/2" \
  "$TMPDIR/${STATION}_${SHOWER}_${YEAR}.mp4"
```

Glob ordering is lexicographic, and the date-in-filename gives chronological order.
At `-framerate 1/3` a 17-night Geminid run is ~51 seconds, growing 3 seconds a day.

Build in `/tmp` and discard after upload. Only the JPEGs are worth keeping.

### 5. Publish and retire

**Order matters. Upload first, delete last.**

1. `videos.insert` — new cumulative video
2. `playlistItems.insert` — add to the station playlist
3. `playlistItems.delete` — remove the **day-before-yesterday's** entry
4. `videos.delete` — delete the **day-before-yesterday's** video
5. Shift the generations in the state file and write it

If step 1 or 2 fails, both existing generations are still live and correct, merely
stale by a night. Delete-first would leave the playlist empty on any network hiccup —
these are Pis on observatory links.

Steps 3 and 4 are best-effort: a video already gone (deleted by hand, or a half-failed
earlier run) should log and continue, not abort the run. Losing track of one orphan is
better than wedging the daily rebuild.

### State file

Because the media cannot be swapped, the script must remember what it is retiring.
Persisting IDs avoids ever calling `playlistItems.list` or `search.list` to rediscover
them.

`generations` is newest-first and holds at most two entries. Each run prepends the new
one and retires anything that falls off the end.

```json
{
  "IL0001/GEM/2026": {
    "generations": [
      {"video_id": "abc123", "playlist_item_id": "PLxyz...", "night": "20261214"},
      {"video_id": "def456", "playlist_item_id": "PLuvw...", "night": "20261213"}
    ],
    "nights": 11,
    "first_night": "20261204",
    "last_night": "20261214"
  }
}
```

Write the state file **after** the uploads and **before** the deletes, so a crash
mid-run orphans a video rather than losing the ID of a live one. An orphan is visible
in the playlist and can be cleaned up by hand; a lost ID cannot be recovered without
`search.list`, which the quota will not tolerate.

### Title format

Put the date range and count in the title so currency is obvious at a glance:

```
IL0001 · Geminids 2026 · Dec 4–14 · 87 meteors
```

## Season end

Stop replacing when the station stops detecting the shower; the last published video is
already the complete cumulative summary.

The rule is **N consecutive nights (default 3) with no detections clearing
`MIN_METEORS`**, and nothing else. No calendar end date, consistent with deriving
activity from detections in the first place: the station decides a shower is over the
same way it decided it had started.

A clouded-out station simply stops updating and keeps its last video, which is correct
— it has nothing new to show. If it clears and the shower is still running, detections
resume and so does the rebuild, with the gap visible in the video as a date jump.

When an entry retires, leave both live generations in place. They are the finished
record. Only the state entry goes away.

## Quota

Uploads no longer bill against the general pool. Current allocation per Google Cloud
project: **100 `videos.insert` calls/day** in a dedicated bucket at 1 unit each, 100
`search.list` calls/day, and 10,000 units/day for everything else combined.

Per station per shower per day:

| Call | Cost |
|---|---|
| `videos.insert` | 1 (dedicated bucket) |
| `playlistItems.insert` | 50 |
| `playlistItems.delete` | 50 |
| `videos.delete` | 50 |

6 stations × 1–2 active showers ≈ 6–12 videos in flight, ~1,800 general units per
day. Comfortably inside both limits, with headroom for the bolide pipeline and for
growth to ~10 cameras. Retiring a cycle late does not change this: the same three
general-pool calls happen per shower per day, just against an older generation.

> **This table is unverified and the design depends on it.** The published cost of
> `videos.insert` is **1600 units against the shared 10,000/day pool**, which is what
> the bolide pipeline was sized against. If that figure applies here, 6–12 uploads a
> day is 9,600–19,200 units and exhausts the pool before the bolide uploads run at
> all — daily cumulative rebuilds would then be impossible as designed, and the
> fallback is fewer stations, fewer showers, or a longer rebuild interval. Read the
> actual limits on the project's Cloud console quota page before building anything.

Never use `search.list` to find existing videos — 100 units against a 100/day cap.
The state file makes it unnecessary.

## Failure modes

**Missed morning.** If the script fails or the station is down, that night's stack is
never generated, and once the night folder is reaped it cannot be recovered.

Handling, in order of effort:
1. Accept the gap. The video skips a night, visibly, since each frame is date-stamped.
2. Log stack-extraction failures separately from upload failures so runs of them are
   noticeable.
3. Optional `pending.json` of nights needing a stack, retried at the start of each
   run. Only helps while the night folder still exists — pair with a generous
   `arch_dir_quota`.

Check what the stations' quotas actually give. If archived nights survive a week or
more, a one-day retry window covers essentially every realistic failure.

**Upload failure.** Idempotent by construction: yesterday's video stays published,
state file unchanged, next morning's run rebuilds and retries with one more night.

## December overlap

The Geminid window (Dec 4–20) overlaps Monocerotids (Dec 1–19), σ-Hydrids (Dec 3–20),
Puppid-Velids (Dec 1–15), Comae Berenicids (Dec 4–Jan 30), Northern Taurids (to
Dec 10), Ursids (from Dec 17), and the resuming Antihelion Source (~Dec 10). This is
the stress case for the design and the reason `MIN_METEORS` exists. Expect GEM
everywhere, HYD on nights near its Dec 9 peak, and little else clearing threshold on a
single camera.

These dates are context for sizing, not inputs to the code — nothing reads a calendar.
They matter here only as an estimate of how many showers might clear `MIN_METEORS` on
one night, which is what sets the TrackStack time budget.

## Open questions

- **Playlist granularity.** One playlist per station, or one per shower across the
  network? Per-station is assumed here and matches the existing bolide playlist
  structure.
- **Multi-station composites.** `trackStack()` accepts a list of directories and
  merges stations onto a common canvas. This is out of scope because it cannot run in
  a per-station external script — it needs a central collector. Worth revisiting if
  the association wants a network-wide artifact.
- **Frame rate.** 1/3 is a guess. May want slower for short showers, faster for
  something like COM that runs Dec 4–Jan 30.
- **Retention of the JPEG archive.** Currently unbounded. Under a GB/year, so no
  action needed yet, but worth a policy eventually.

## References

- RMS `Utils/ShowerAssociation.py` — `showerAssociation()` returns
  `(associations, shower_counts)`
- RMS `Utils/TrackStack.py` — `trackStack()`; requires
  `platepars_all_recalibrated.json`
- RMS `RMS/DeleteOldObservations.py` — quota-driven cleanup, `getNightDirs()`
- IMO Working List of Visual Meteor Showers, 2026 Meteor Shower Calendar (ed. Rendtel)
  — background for the December overlap sizing only; nothing in the pipeline reads it
- YouTube Data API v3 quota reference