---
name: station-fleet-rollout
description: State of the IMN station fleet and what is true about the Pis themselves
metadata: 
  node_type: memory
  type: project
  originSessionId: 6b51fe97-05d2-4839-bcea-0637a2eb9c78
  modified: 2026-11-19T13:48:50.171Z
---

Fleet facts not derivable from the repo, as of 2026-09-09. The `imn` script
in the repo does the mechanics; this is what it meets on the other end.

Stations are listed in `~/.config/IMN/stations` (NOT in git — it holds
credentials): ip, station, user, tab separated. All are user `pi` except il0001 (`rms`).
The stations are only reachable over the VPN. Passwords are kept out of the repo.

Rollout state:
- **il0003 — LIVE since 2026-09-09, and CONFIRMED PUBLISHING** (checked on
  the channel 2026-09-22). The open thread is closed: it uploaded from real
  nights unattended at ~04:10 on 2026-09-13, -14 (x2) and -15, into both
  `IMN/IL0003` (id `PLK6rhwsi03rM` — short ids are valid, that is a real
  playlist) and the `common_playlist_id` playlist. Two earlier videos from
  the 2026-08-18 night were pushed by hand on 2026-09-09.
  Then it stopped — see [[youtube-oauth-token-expiry]].
- il0002, il0008, il0009 — reachable, NOT yet provisioned.
- **il0001 — PROVISIONED 2026-11-19** (`imn --init il0001`), first unattended
  run due ~04:10 on 2026-11-20, not yet verified. Was unreachable until the
  user fixed a firewall issue. Differs from the others: user is **`rms`**
  (not `pi`; key auth not set up) and `~/vRMS` is
  **Python 3.13.5**, not 3.7. The requirements installed fine there.
- il0004 — was unreachable (down or off VPN) on 2026-09-09; not rechecked.

The Pis:
- RMS venv is `~/vRMS`, **Python 3.7.3 with pip 20.0.2**. That pip uses the
  legacy resolver, which does not backtrack — it happily installs a
  combination upstream declares incompatible and only warns. That is why
  `requirements-bolides.txt` carries google-auth's exclusions explicitly.
  google.api_core also warns that 3.7 is unsupported; it still works.
- Code lives under `~/source` (`RMS`, `cmn_binviewer`, and now `IMN`).
- The OLD IMN was files inside `~/source/RMS/IMN`, not its own checkout.
  `--clone` leaves it in place, so reverting a station is just
  `cp ~/source/RMS/.config.imn-backup ~/source/RMS/.config`.
- il0002 and il0003 had a station-LOCAL edit to the old `IMN.sh`: a second
  curl uploading the radiants file as `latest.txt`. il0008/il0009 did not.
  Switching to the clone drops it. DECIDED 2026-09-09: don't care, the
  FTP service is believed dead. CONFIRMED dead by the user 2026-09-22, and
  `IMN.sh` was deleted along with its call site — see
  [[imn-station-architecture]].
- `client_secret.json` is NOT needed on a station: it is read only by
  `youtube.py authorize()`, which needs a browser. `youtube.json` already
  carries client_id + client_secret + refresh_token.

Two uploads are easily confused (checked 2026-09-10):
- **RMS's own upload to the GMN server** is `RMS/UploadManager.py`, entirely
  internal to RMS. It has nothing to do with IMN.py and is working fine.
- **iStream** is what `IMN.py` shells out to, and it is effectively DEAD:
  absent from il0002/il0003/il0009; installed on il0008, where its newest
  `iStream_log_*` is from 2023-06-02 despite being called nightly since.
  DECIDED 2026-09-10: keep the call, guarded, rather than delete it —
  il0001/il0004 are unreachable and unverified.

THE REBOOT-LOCK HAZARD, worth remembering before touching `rmsExternal`:
RMS runs it in a `multiprocessing.Process` it starts and never joins, so
exceptions there are INVISIBLE (no traceback in the RMS log). RMS only
waits on `~/RMS_data/.reboot_lock` and never clears it — only IMN.py does.
So any crash between taking and releasing the lock silently stops the
station rebooting forever. It is now released in a `finally`, and helper
scripts go through `run_script()` because `subprocess.call` RAISES on a
missing script where the old `os.system` merely returned 127.

See [[bolide-video-publishing]] and [[imn-station-architecture]].
