---
name: imn-station-architecture
description: What the IMN repo is and how RMS invokes it on GMN stations
metadata: 
  node_type: memory
  type: project
  originSessionId: 7bf81146-7be3-4447-bfbe-f7f5ba604e20
  modified: 2026-09-22T08:47:09.419Z
---

IMN = Israeli Meteor Network station scripts. Runs on IL00xx GMN stations
(Raspberry Pi, RMS software). RMS calls `IMN.py:rmsExternal()` via
`external_script_path` in the station `.config`, after nightly processing.

Flow: `IMN.py` touches a reboot lock -> `../iStream/iStream.sh` ->
`bolides.publish_bolides()` -> `trackstacks.publish_shower_stacks()` ->
drops the lock.

**The astronomy.org.il FTP server is dead and is not coming back** (user,
2026-09-22). `IMN.sh`, whose entire body was the `*_radiants.txt` curl
upload to `ftp://ftp.astronomy.org.il/IMN/radiants/<camera>/`, was deleted
along with its call site in `IMN.py`. Consequence: **there is now no path
off a station except the YouTube API.** Anything that wants to publish
data (e.g. JSON manifests for a web page) needs a new transport chosen
first — that is what shelved `.claude/plans/web-manifests.md`. Candidates weighed:
a repo with GitHub Pages (stations already have a git checkout and
`imn --update` already pulls), an HTTP endpoint the association would have
to stand up, or no station-side publishing at all with the page reading
YouTube via a referrer-restricted key in a *separate* GCP project.

The iStream call uses `config.rms_root_dir` (location-independent), so it
works whether IMN is under RMS or a sibling. Stations have IMN as a
sibling of RMS; `iStream` stays under RMS.

Credentials must never be committed (there was a git-history issue). See
[[bolide-video-publishing]], [[station-fleet-rollout]].
