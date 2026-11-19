---
name: youtube-oauth-token-expiry
description: "The IMN refresh token dies every 7 days while the OAuth consent screen is in Testing, silently stopping all station uploads"
metadata: 
  node_type: memory
  type: project
  originSessionId: 39c6c854-f05a-4c27-8c7b-716d9224d8bd
  modified: 2026-11-19T13:43:19.420Z
---

The fleet shares ONE refresh token, in `~/.config/IMN/youtube.json`, pushed
to every station by `imn --init`/`--update`. When it dies, every station
stops publishing at once.

It died once already: minted 2026-09-09 13:59, last successful station
upload 2026-09-15 04:16, dead by 2026-09-22 with
`RefreshError: invalid_grant: Token has been expired or revoked`.

**Why:** Google expires refresh tokens after **7 days** for any OAuth client
whose consent screen is still in *Testing* publishing status. 09-09 + 7d =
09-16, and nothing published after 09-15. Strongly corroborated, not proven
(no upload on 09-16 either, which could have been a quiet night).

**RESOLVED 2026-09-22.** Project `israeli-meteor-network` is now
Publishing status *In production*, User type External, and re-authorized
afterwards. The banner "Your app requires verification" is expected and
harmless: **publishing removes the expiry; verification is a separate
gate** that only removes the unverified-app interstitial and the 100-user
cap, neither of which matters for one account. Do NOT chase verification —
it needs a real website with privacy policy and ToS, and astronomy.org.il
is still being built (~months, as of 2026-09).

The expiry is stamped on the token AT ISSUE, so a token minted while the
project was in Testing keeps its 7-day clock even after publishing. Any
re-auth must come AFTER the status flip. This bit us once: the 2026-09-22
11:52 token had to be thrown away and re-minted at 13:33.

First real test of the fix: uploads should still work after 2026-09-29.
Nothing in the token says when it expires, so time is the only proof.
Checked 2026-09-28: il0003 carries the 09-22 13:33 token (same refresh
token as local), refreshes fine, resolves to the IAA channel, and uploaded
two clips at 04:13 that morning. Still day 6, so not yet the proof — look
again after 2026-09-29 13:33.

**PROVEN 2026-11-19:** the 2026-09-22 token is ~58 days old and still
refreshes fine from il0001 (a station that never had the old token). The
7-day expiry is gone for good; this is no longer a live risk, only the
silent-failure mode below if the token is ever revoked.

**Why it is easy to miss:** the failure is silent. `get_uploader()` builds
`Credentials` without any network call, so it succeeds; the refresh only
happens at upload time, where `bolides.publish_bolides()` catches the
exception and logs `upload failed for ...`. The station finishes the night
normally with rendered mp4s and nothing published. Nothing alerts.

Re-auth is `python youtube.py authorize` on a machine with a browser (needs
`client_secret.json`, which is NOT on the stations), then redistribute
`youtube.json`. Pick the IAA brand channel and tick BOTH consent boxes.

See [[station-fleet-rollout]], [[bolide-video-publishing]].
