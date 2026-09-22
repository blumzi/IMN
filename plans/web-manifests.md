# Plan: Web Manifests for a Station/Month Browser

**Status:** SHELVED — no transport. See "Why this is shelved".
**Date:** 2026-09-22
**Component:** would have been a new `manifest.py`, called from `bolides.py` and `trackstacks.py`

## Why this is shelved

The design assumed the stations could publish small JSON files to
`ftp.astronomy.org.il`, the way `IMN.sh` published the nightly radiants file.
**That server is dead and is not coming back.** `IMN.sh` has been deleted along
with its call site in `IMN.py`; there is now no path off a station except the
YouTube API itself.

Nothing here is wrong, and none of it depends on FTP specifically — it needs *a*
place to put a few kilobytes of JSON per station per night. It is written down so
that if a host appears, the design does not have to be rediscovered. The
period-split playlists it was paired with did not need a transport and shipped
separately: see `plans/playlist-periods.md`.

**The blocker is one question: where do the files go?** Candidates considered:

- A repo with GitHub Pages. Stations already have a git checkout and `imn --update`
  already pulls, so half the machinery exists. Per-station files mean concurrent
  pushes collide only at the ref, so `pull --rebase` and retry is enough. Costs a
  deploy token per station.
- An HTTP endpoint the association controls. Simplest for the station, but there
  isn't one today.
- No manifests at all: a referrer-restricted browser API key reading
  `playlists.list` / `playlistItems.list` at 1 unit per 50. Must be a *separate*
  GCP project, or page traffic competes with the upload budget. Loses the ranking
  metadata unless the page parses it back out of the video descriptions, which
  already carry peak magnitude and trail length.

## Goal (as designed)

Give astronomy.org.il a page with a "latest" strip and a station/month browser,
with no API key, no server, and no two stations ever writing the same file.

The Atom feed (`videos.xml?playlist_id=`) needs no key but exposes only ~15
entries, so it can drive a "latest" strip and nothing more. The manifests existed
because it cannot do the rest.

## Design decisions

### No shared files, anywhere

The original notes proposed `imn/latest.json` (rolling N across all stations) and
`imn/index.json` (which station/month pairs exist). Both are shared mutable state,
and the station side should not write shared mutable state under any transport
that is merely a file PUT:

- Every station finishes its night within the same hour, so writes collide.
- A plain upload has no lock, no compare-and-swap, and no read-modify-write that
  survives two concurrent writers.
- A station that read-modify-writes a shared file silently drops the entries
  another station had just added.

So every file has exactly one writer:

| File | Writer |
| --- | --- |
| `web/<station>/<YYYY-MM>.json` | that station |
| `web/<station>/index.json` | that station |
| `web/<station>/latest.json` | that station |
| `web/stations.json` | the operator, via `imn` |

The landing view becomes: fetch `stations.json`, fetch each station's
`latest.json`, merge and sort client-side. Ten small, individually cacheable files
instead of one contended one. `stations.json` changes only when a station joins the
fleet, which is already a manual step.

(A git-based transport would make this constraint softer, since git *does* merge —
but per-station files are still the version that never needs a conflict resolved
at 05:00 on an unattended Pi.)

### Manifests live in `~/imn`, not under the night directory

`bolides.publish_bolides()` writes `manifest.json` into
`<archived_dir>/IMN_bolides/`. That is inside `RMS_data`, which RMS reaps to stay
inside its quota — the same reason track stacks archive to `~/imn/stacks`. The
per-night manifest stays where it is (it is a debugging artifact for that night);
the *web* manifests would go to `~/imn/web/`.

### The month comes from the capture night, not from today

Two reasons it cannot use `datetime.now()`:

- The nightly hook runs after dawn, so a night captured on the 31st is published on
  the 1st and would land in the wrong month.
- Reprocessing an old night would file it under the month it was reprocessed in.

"Night" means RMS's night, i.e. the date in the archived directory name — *not* the
FF file's UTC date. A bolide at 01:30 UTC on Oct 1 belongs to night `20260930`,
month `2026-09`, which is where the rest of that night's products already are.

This part shipped: `bolides.night_of()` (moved there from `trackstacks._night_of`,
since `trackstacks` imports `bolides` and not the other way round).

### Track stacks bucket by the shower's FIRST night

A track-stack entry is replaced every morning while the shower is active. If it is
filed under the month of its *last* night, a shower crossing a month boundary
(Quadrantids run Dec 28 – Jan 12) writes into December, then moves to January —
and December's file is left pointing at a video that `retire_video` has since
deleted, with nothing scheduled to ever rewrite it.

Filing under the *first* night means one file, rewritten in place for the shower's
whole run. The first night is free: `stack_paths()` is already sorted, so
`images[0]`'s date is it, exactly as `_title()` reads it today.

## The manifest schema

`~/imn/web/<station>/<YYYY-MM>.json`:

```json
{
  "schema": 1,
  "station": "IL0003",
  "month": "2026-09",
  "updated": "2026-09-22T04:12:07Z",
  "entries": [ ... ]
}
```

Entries are newest-first. Every entry carries the common block; `kind` selects the
extras. Thumbnails are *not* stored — the page derives
`https://i.ytimg.com/vi/<video_id>/hqdefault.jpg`, and embedding with
`?list=<playlist_id>` gives next/prev within the month in our own player.

**Common to every entry**

| Field | Notes |
| --- | --- |
| `id` | stable identity, used to upsert; see below |
| `kind` | `"bolide"` or `"trackstack"` |
| `station` | `IL0003` |
| `night` | `"2026-09-21"` — RMS's night, not the UTC date of the frame |
| `video_id` | |
| `playlist_id` | the period playlist this sits in |
| `playlist_item_ids` | every placement, for a later retire |
| `title` | the YouTube title, so the page needs no second source |
| `updated` | when this entry was last written |

**`kind: "bolide"`**

| Field | Notes |
| --- | --- |
| `time` | `"2026-09-22T01:14:37Z"`, from `filenameToDatetime(ff_name)` |
| `peak_mag` | apparent, most negative frame |
| `sat_fraction` | `null` when the FTPdetectinfo carries no saturation column |
| `trail_deg` | |
| `ff_name` | |
| `shower` | `null` — bolides are not currently associated; reserved |

`id` is the `ff_name`: one per bolide, stable, and already unique per station-night.

**`kind: "trackstack"`**

| Field | Notes |
| --- | --- |
| `shower` | `"PER"` |
| `year` | `"2026"` |
| `first_night` / `last_night` | the run covered by this video |
| `nights` | frames in the video |
| `meteors` | cumulative associations |

`id` is `"<shower>-<year>"`. The entry is upserted under that id every morning, so
the file always names the live video.

`~/imn/web/<station>/index.json` — which months this station has, so the selector
only offers months with content:

```json
{"schema": 1, "station": "IL0003", "months": ["2026-08", "2026-09"],
 "updated": "2026-09-22T04:12:07Z"}
```

`~/imn/web/<station>/latest.json` — the newest `web.latest` entries for this
station, same entry shape, rebuilt from the two most recent month files. The
landing view merges these across stations.

`web/stations.json` — operator-written, from `~/.config/IMN/stations`:

```json
{"schema": 1, "stations": [{"code": "IL0003", "name": "...", "lat": 0, "lon": 0}]}
```

## The writer hook

A new module `manifest.py` owning the tree and the upload; `bolides` and
`trackstacks` only hand it entries.

```python
def record(station, month, entries):
    """ Upsert entries into one month file, then refresh index and latest and
        push all three.

        Best effort throughout, like every other step in the nightly hook: a
        manifest that fails to write must not cost us the night's uploads,
        which have already happened by the time we are called.
    """
    if not imnconfig.get("web", "enabled"):
        return

    try:
        path = os.path.join(_root(), station, month + ".json")
        doc = _read(path) or {"schema": SCHEMA, "station": station, "month": month,
                              "entries": []}

        by_id = {e["id"]: e for e in doc["entries"]}
        for entry in entries:
            by_id[entry["id"]] = entry          # upsert: track stacks replace daily

        doc["entries"] = sorted(by_id.values(),
                                key=lambda e: (e["night"], e.get("time", "")),
                                reverse=True)
        doc["updated"] = _now()

        _write(path, doc)                       # atomic: tmp + os.rename
        _write_index(station)                   # just a directory listing
        _write_latest(station)                  # newest N over the last two months
    except Exception as e:
        log.error("could not update manifests for %s/%s: %r", station, month, e)
```

Call sites: once in `bolides.publish_bolides()` after the upload loop, and once in
`trackstacks.publish_shower_stacks()` after `save_state()` and `_retire_old()` —
that ordering matters, since the video a manifest names must be one that is
inserted and not retired.

**Batch the publish, don't push per call.** `publish_shower_stacks` calls `record()`
once per active shower; in December that is six, each re-pushing `index.json` and
`latest.json` only for the next to supersede it. Accumulate during the night and
publish once, from `rmsExternal` after both publishers have run.

**`publish_video` would need to return placements**, i.e.
`[{"playlist_id": ..., "item_id": ...}]` rather than a flat list of item ids, since
an entry needs the playlist id too. That changes the shape of `generations[]` in
the track-stack state file, and stations have live state files, so `_retire_old()`
would have to read both shapes. None of this was done — the shipped playlist split
deliberately left the return shape alone, because only the manifests needed it.

## Open questions

1. Where do the files go? (The blocker. See above.)
2. Should bolide entries carry a shower association? The field is reserved;
   `trackstacks.analyse_night()` already computes the mapping for the same night,
   so it would be a matter of threading it across rather than new work.
