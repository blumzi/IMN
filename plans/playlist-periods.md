# Plan: Period-Split YouTube Playlists

**Status:** Implemented
**Date:** 2026-09-22
**Component:** `youtube.py`, with call-site changes in `bolides.py` and `trackstacks.py`

## Goal

Keep the channel navigable as it grows. One playlist per station accumulates
every bolide that station has ever published; splitting it by period keeps each
playlist a readable length and lets the channel page render station → month.

## The constraint

YouTube playlists are flat and cannot nest. The only namespace available is the
playlist title, which is what `youtube.py` already exploited with `IMN/{station}`.
So the period goes in the title too, and the one visible level of nesting comes
from channel sections (see Operational notes).

## Design decisions

### The title is a template over period keys

`playlist_title` is formatted against `{station}` plus whatever period keys the
caller supplies — `{night}`, `{month}`, `{year}`, and `{shower}` for track stacks:

```json
"playlist_title":            "IMN/{station}/{month}",
"trackstack_playlist_title": "IMN/{station}/{shower}-{year}"
```

Months are zero-padded (`%Y-%m`), so titles sort lexicographically — the only
ordering the channel page offers.

### Splitting is opt-in, and the old behaviour is the default

`str.format` ignores keys a template does not mention, so a station still
configured with `IMN/{station}` keeps resolving to exactly the playlist it uses
today. The module default is likewise still `IMN/{station}`
(`youtube.DEFAULT_PLAYLIST_TITLE`), so a station that merely `git pull`s does not
silently start creating playlists — the split begins when the operator pushes a
`youtube.json` carrying the new templates, which `imn --update` already does
alongside the pull. No migration, no flag day, and the rollout is per station.

A template naming a key the caller does not supply is a config typo. It logs and
falls back to the flat title rather than raising: a typo must not cost a night's
upload its playlist.

### Templates are per upload kind

`publish_video(..., kind=...)` selects `"<kind>_playlist_title"`, falling back to
`"playlist_title"`. `youtube.py` stays ignorant of what a kind means; it only
knows the config key naming rule.

The per-kind split exists because the natural period differs. A bolide belongs to
a month. A track stack is one running video per shower per year, replaced every
morning — filing it by month would scatter one shower's generations across two
playlists whenever it crossed a boundary, which the Quadrantids (Dec 28 – Jan 12)
do every year.

### Periods come from the capture night, never from today

`bolides.night_of()` reads the date out of the archived directory name. Two
reasons `datetime.now()` is wrong:

- The nightly hook runs after dawn, so a night captured on the 31st is published
  on the 1st and would land in the wrong month.
- Reprocessing an old night would file it under the month it was reprocessed in.

"Night" means RMS's night, *not* the UTC date of any one frame. A bolide at
01:30 UTC on Oct 1 belongs to night `20260930`, month `2026-09`, alongside the
rest of that night's products.

`night_of()` and `playlist_keys()` live in `bolides.py` because `trackstacks`
imports `bolides` and not the other way round; `trackstacks._night_of()` was
removed in favour of them.

### The playlist-id cache is scoped, not dropped

`authorize()` used to do `existing.pop("playlists", None)`, because playlist ids
belong to whichever channel was authorized. Correct but blunt: re-consenting to
the *same* channel — the common case, e.g. after a scope change — threw away a
cache that then cost a full cold scan to rebuild, and with station × month there
will eventually be hundreds of playlists to page through.

It now records the channel id (`channels.list(mine=True)`, 1 unit, and only in
the one-time interactive flow) and drops the cache only when the channel differs
or cannot be determined. That is the right answer to the actual hazard; a sidecar
cache file has the same staleness problem plus a second place to keep in sync.

### A cold lookup primes the whole cache

`_find_playlist()` caches every title it sees while paging, instead of returning
on first match. A cold start costs the same page-throughs it always did and
resolves *every* playlist, so the second miss of the run is free. This is what
makes hundreds of playlists cheap rather than merely survivable.

### Quota

Splitting is free; the surrounding budget is not, and it is worth stating
precisely because the note this plan came from said only "quota is not a concern":

| Call | Units | Frequency |
| --- | --- | --- |
| `videos.insert` | 1600 | per video |
| `playlistItems.insert` | 50 | per video per playlist |
| `playlists.insert` | 50 | once per station-period |
| `playlists.list` | 1 | per 50 playlists, cold cache only |
| `videos.delete` | 50 | per retirement |

50 units once per station-month against the 1600 already paid per upload is
nothing. But the default cap is 10,000 units/day — **six uploads a day for the
entire fleet**. Six stations at `bolides.n = 3` is 28,800 units before track
stacks. Either the project holds a quota extension or `n` is effectively capped
by quota rather than by config. Worth confirming before the fleet grows.

## Not done

**A rolling "latest" playlist.** Membership is by reference, so a video can sit in
`IMN/IL0003/2026-09` and a capped `IMN/Latest` for one extra
`playlistItems.insert` — this generalizes the existing `common_playlist_id`, which
already works exactly that way and is untouched. Trimming needs no stored state:
`playlistItems.list` returns items in position order for 1 unit and inserts land
at the end, so "delete from position 0 until the length is N" is enough.

Left out because it costs 50 units a video plus 50 a trim against a budget that is
already the binding constraint, and because the web page was to have covered the
same need for free — see `plans/web-manifests.md`, now shelved.

## Operational notes

- **Channel sections** give the one visible level of nesting: a "Multiple
  playlists" section per station, holding that station's monthly playlists,
  renders as station → month on the channel page. The `channelSections` API
  documents a limit of 10; Studio allows 12. With ~10 stations projected, budget
  one section per station and nothing else. Configured by hand in Studio →
  Customization → Layout; nothing here automates it.
- **Rollout:** push the new `youtube.json` with `imn --update`. Each station
  creates its month playlist on its first upload of the month and caches the id.
- **The first upload after a re-auth** does a full `playlists.list` scan. Expected,
  once, and now it primes everything.
