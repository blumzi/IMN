# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

The Israeli Meteor Network (IMN) station add-on for [RMS](https://github.com/CroatianMeteorNetwork/RMS). RMS runs on each station's Raspberry Pi; at the end of every night it calls `IMN.py:rmsExternal()` (configured by `external_script_path` / `external_function_name` in the station's RMS `.config`). From there IMN publishes the night's best bolides — and, when enabled, cumulative shower track-stack videos — to the association's YouTube channel.

There is no build, lint config or test suite. Changes are verified by running the scripts against a real archived night (see Commands), and ultimately on a station.

## Target environment — the binding constraint

Code runs on the stations, not the dev machine:

- **Python 3.7** in the RMS venv `~/vRMS` (no walrus, no `dict | dict`, no 3.8+ stdlib APIs). `tomllib`/new `tomli` are unavailable there, hence the parser chain in `imnconfig._read_toml`.
- **NumPy 1.x, ffmpeg 4.1.4** (no `tpad` filter — see `bolides._retime`), the stations' pinned matplotlib (saves blank images — see `trackstack_runner.py`), pip 20 with the legacy resolver (see comments in `requirements-bolides.txt`).
- **RMS is vendored third-party code and is never modified.** IMN imports its `RMS.Formats` readers, runs its `Utils.*` as subprocesses from `config.rms_root_dir`, and works around its bugs from our side (staging directories of symlinked FF/FR files, intercepting `plt.imshow`, our own FR reader for NumPy 2 on dev machines). Document any such workaround where it lives.

Everything in the nightly hook must be failure-tolerant: `rmsExternal` holds RMS's reboot lock, which RMS only ever *waits* on, so an exception or hang there can stop a station rebooting. Each stage is wrapped in try/except, subprocesses get timeouts, and the lock is removed in a `finally`.

## Architecture

- `IMN.py` — the RMS hook: iStream (if present) → `bolides.publish_bolides` → `trackstacks.publish_shower_stacks`.
- `bolides.py` — select from `FTPdetectinfo` (peak apparent mag ≤ threshold; rank by saturated-frame fraction, then brightness, then trail length; top N) → render with RMS `FRbinViewer`, falling back to compositing FR crops ourselves → retime (slow down + hold last frame) → upload → `IMN_bolides/manifest.json` in the night dir. Also owns `night_of()` and `_station_for()`, which `trackstacks.py` reuses.
- `trackstacks.py` + `trackstack_runner.py` — a shower is "active" when this station associated ≥ `min_meteors` to it last night (no calendar). Stack that shower's FFs (subprocess, wall-clock timeout, nightly budget), archive the JPEG under `~/imn/stacks` (outside `RMS_data`, which RMS reaps), rebuild the cumulative video with ffmpeg, publish, and retire older generations one cycle late. State lives in `~/imn/trackstacks.json`, saved before anything is retired.
- `youtube.py` — Data API v3 uploader. Playlist titles are templates (`IMN/{station}/{month}`, `IMN/{station}/{shower}-{year}`, per-kind via `<kind>_playlist_title`); playlist ids are cached in `youtube.json`. The daily quota is small, so avoid `search.list` and persist ids you'll need later.
- `imnconfig.py` — tunables from `~/.config/IMN/config.toml` (`$IMN_CONFIG`). Every key needs an entry in `DEFAULTS`; bad files/keys/types log and fall back. Named `imnconfig` because RMS puts this directory first on `sys.path`, so a `config.py` would shadow other modules.
- `imn` — bash fleet runner (`--test`, `--log`, `--init`, `--update`) over ssh with `sshpass`.

Conventions that span files:
- **Dates come from the capture night** (the `<STATION>_<YYYYMMDD>_...` archive dir name via `bolides.night_of`), never from today — the hook runs after dawn.
- **Station code comes from the data** (FF/FR file names), with `config.stationID` only as fallback.
- Credentials (`youtube.json`, `client_secret.json`, the `stations` file) live in `~/.config/IMN`, never in the repo; `config.toml` holds tunables only. `imn` copies only `youtube.json` and `config.toml` to stations.

## Commands

Local dev venv (`.venv`, managed with uv):

```
uv pip install -r requirements-bolides.txt
```

Bolide pipeline against an archived night (needs an RMS checkout on `PYTHONPATH` and its `.config`):

```
python bolides.py <archived_night_dir> --dry-run            # selection only
python bolides.py <archived_night_dir> --no-upload -c <dir> # also render mp4s
```

One-time YouTube auth (on a machine with a browser; writes `~/.config/IMN/youtube.json`): `python youtube.py authorize`

Fleet (stations: IL0001, IL0002, IL0003, IL0008, IL0009; reached by bare lowercase hostname over the VPN; user `pi` unless the stations file says otherwise):

```
./imn --test   [station ...]   # reachability
./imn --log    [station ...]   # fetch last night's log into ./imn-logs and show the hook's lines
./imn --init   [station ...]   # clone to ~/source/IMN, install deps, repoint external_script_path
./imn --update [station ...]   # git pull + deps + push youtube.json/config.toml
```

`imn` is the one way to reach the fleet; it needs `sshpass`. `--init`/`--update` push `youtube.json` from the local `~/.config/IMN`, so that file must exist where `imn` runs.

### Fleet status (as of 2026-10-06)

| Station | IMN | Notes |
|---|---|---|
| IL0001 | ? | `pi` and `rpi` with the usual password both fail; real credentials unknown |
| IL0002 | installed 2026-10-06 | `imn --init` + `youtube.json` from IL0003; no iStream |
| IL0003 | installed | the original test station; track stacks have run here |
| IL0008 | ? | resolves on the VPN but port 22 times out |
| IL0009 | installed 2026-10-06 | `imn --init` + `youtube.json` from IL0003; no iStream |

Keep this table current when the fleet changes.

On a station, IMN lives at `~/source/IMN` (sibling of `~/source/RMS`); logs are `~/RMS_data/logs/log_*.log`.

## Repo notes

- `plans/` holds design docs with a Status header (Implemented / Draft / Shelved). Write one for non-trivial features; they record *why*, which the code comments then reference.
- `Config` is a sample station RMS config (IL0003); `StationSetup.txt` is the manual station bring-up checklist (its IMN step predates `imn --init`).
- Work goes through feature branches and PRs into `main`.
- **Claude's own files live in `.claude/` and are tracked** — this file and settings. Put lasting project notes here rather than in Claude Code's private auto-memory. The repo is on GitHub: no passwords, tokens or VPN addresses (those stay in `~/.config/IMN`).
