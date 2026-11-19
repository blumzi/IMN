---
name: bolide-video-publishing
description: In-progress feature — publish best-of-night bolide videos to YouTube
metadata: 
  node_type: memory
  type: project
  originSessionId: 7bf81146-7be3-4447-bfbe-f7f5ba604e20
  modified: 2026-09-09T17:45:16.725Z
---

In-progress IMN feature: select each station's best-of-night bolides and
publish videos to a playlist on the Israeli Astronomical Association
YouTube channel.

Selection (per-station, NO cross-station calibration):
- N configurable, default 3.
- Threshold: peak *apparent* magnitude <= -1 (apparent, not absolute —
  it's what the website displays).
- Ranking: saturated-frame fraction primary; angular trail length as
  tiebreak.

Source data lives in `ArchivedFiles`: `FR_*.bin`, matching `FF_*.fits`,
`FTPdetectinfo_*.txt`.

Blocker (small): `archived_night_dir` is ALREADY available inside
`rmsExternal(captured_night_dir, archived_night_dir, config)` — RMS
passes both dirs (RMS/RMS/RunExternalScript.py) and IMN.py already
forwards `archived_night_dir` to `iStream.sh`. It is only dropped on the
`IMN.sh` call (`IMN.py:32` passes captured dir only). Fix = forward it and
teach `IMN.sh` to accept a 2nd arg (its guard is hardcoded `$# -ne 1`,
and `cd ${captured_dir}` still has no error check).

RMS source cloned at `~/src/RMS` for reference.

IMPLEMENTED (2026-08-31, uncommitted at time of writing):
- `bolides.py` — selection + render + orchestration. Selection reads
  FTPdetectinfo (RMS.Formats) — peak apparent mag = min per-frame `mag`,
  sat_fraction = frames with saturated_count>0 / n_frames, trail via
  RMS.Math.angularSeparationDeg on first/last ra,dec. Fallback to
  brightness ranking if the whole night lacks saturation data. Selection
  needs NO pixel parsing — FTPdetectinfo already carries mag + saturated_count
  per frame. Render stages only the selected FR (+FF+FTP) into a temp dir and
  runs `sys.executable -m Utils.FRbinViewer <stage> -e -f mp4 -x -c <cfg>`
  with cwd=config.rms_root_dir (same vRMS interpreter rmsExternal runs in).
- `youtube.py` — YouTube Data API v3 upload + playlist add via OAuth refresh
  token. Creds at `~/.config/IMN/youtube.json` (NEVER committed; gitignored;
  template `youtube.json.example`). `python youtube.py authorize` mints the
  refresh token from `~/.config/IMN/client_secret.json`. Default privacy
  'unlisted'. get_uploader() returns None (skip upload) if creds/libs absent.
- `IMN.py:rmsExternal` calls `bolides.publish_bolides(archived_night_dir,
  config)` in a try/except so failures never break RMS's night or the reboot
  lock. Feature is inert until creds are placed.
- Deps: `requirements-bolides.txt` (google-api-python-client, google-auth,
  google-auth-oauthlib) — install into vRMS.
- Test standalone: `python bolides.py <archived_night_dir> --dry-run`.
- Selection/ranking/threshold/fallback logic unit-tested with synthetic data.

LIVE AS OF 2026-09-09 — the whole path has been exercised against real
IL0003 data and two clips are published on the channel:

- Authorized against the **IAA brand channel** "האגודה הישראלית
  לאסטרונומיה" (UCX5ON4Xd71Ad6TygpabouAg), NOT the personal one. The
  channel is chosen on the OAuth consent screen's "Choose a channel"
  step; a first attempt silently authorized the personal channel.
  `authorize()` now passes `prompt="consent"` (Google only ever issues a
  refresh token on FIRST consent, and otherwise skips the chooser) and
  aborts rather than writing a config with no refresh token.
- BOTH consent checkboxes must be ticked: `youtube.upload` alone can
  upload but cannot touch playlists, and oauthlib then rejects the
  reduced-scope token outright.
- Playlists are POINTERS, and YouTube has NO folder hierarchy — playlists
  cannot nest. So "meteors/cameras/<station>" lives in the playlist
  *title*: `playlist_title` default `IMN/{station}`, created public on
  first upload for a station, its id cached in `youtube.json` under
  `playlists` so later nights just append. Grouping them into a shelf on
  the channel page is a manual Studio → Customization → Layout step.
- Every upload ALSO goes into the pre-existing public playlist "The
  Israeli Meteor Network" (`common_playlist_id`,
  PL3F0POS0n9Ams9ehMaVW2zTujm-8REeBW). Videos stay `unlisted` — an
  unlisted video inside a public playlist is watchable by anyone opening
  that playlist.
- QUOTA IS THE REAL CEILING: 10,000 units/day, `videos.insert` costs
  1600 → ~6 uploads/day channel-wide. Playlist ops are 50, lookups 1.

On the channel as of 2026-09-09: exactly two clips, both from the
2026-08-18 IL0003 night, in `IMN/IL0003` (PLK6rhwsi03rM) and the network
playlist. Two earlier test uploads mislabelled `XX0001` were deleted.
NOTE re-running a night that was already published uploads it AGAIN —
nothing consults the manifest to dedupe.

Deployment to stations is done by the `imn` script (merged in PRs #1-#3);
il0003 is live and awaiting its first real night. See
[[station-fleet-rollout]].

Video generation: FRbinViewer stays PREFERRED (it burns in frame number,
timestamp, shower name), with an IMN-side fallback renderer when it
produces nothing — see [[rms-on-apple-silicon]] for why that was needed.
`python -m Utils.FRbinViewer <dir> -e -f mp4 -x`
`-x` is MANDATORY — without it OpenCV opens a window and dies headless
on the Pi.

Every clip is RETIMED before upload (`_retime`, ffmpeg
`setpts=2.0*PTS,tpad=stop_mode=clone:stop_duration=1.5`): a bolide
crosses in well under a second, too fast to watch. Done as a POST step
on whatever the renderer emitted, so station and dev output match —
retiming inside FRbinViewer would mean editing vendored RMS or feeding
it a config whose fps lies (it derives burnt-in timestamps from it).

Station identity comes from the DATA, not the config: `_station_for()`
reads the code out of the `FF_<station>_...` filenames and only falls
back to `config.stationID`. Learned the hard way — a test run from
~/src/RMS picked up the stock `.config` and published two clips titled
"Bolide XX0001", creating an `IMN/XX0001` playlist (since deleted).

CLI flags: `--dry-run` (selection only), `--no-upload` (select+render,
no publish — use this for any local test), `-c/--config <dir-or-file>`.

Credentials for the YouTube upload must NOT be committed.

Also planned: per-station per-night TrackStack summaries
(`Utils/TrackStack`) during major showers — resource-hungry on a Pi.

See [[imn-station-architecture]] for the overall flow.
