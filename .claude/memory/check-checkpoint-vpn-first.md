---
name: check-checkpoint-vpn-first
description: Check the Check Point VPN is down before touching the stations — it hijacks the station routes
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 6b51fe97-05d2-4839-bcea-0637a2eb9c78
  modified: 2026-09-10T15:52:50.556Z
---

Before accessing any IL00xx station, check that the user's **Check Point VPN
is disconnected**. If it is up, every station is unreachable and ssh fails
with "No route to host" — which looks exactly like the stations being down.

**Why:** the stations are reached through the IMN OpenVPN tunnel (its own utun,
which installs a /16 for the station subnet). Check Point (the Weizmann
tunnel) installs a spray of narrower routes over 10/8, including a /17 over
the station subnet. macOS routes by longest prefix, so its /17
always beats the IMN /16 and station traffic is sent into a tunnel where
those addresses do not exist. Reconnecting the IMN VPN does NOT help: its
client only ever installs a /16, which can never win. Diagnosed 2026-09-10
after the whole fleet went unreachable mid-session.

**How to apply:** when ssh to a station fails, do not conclude the station is
down. Run `route -n get <station-ip> | grep -E "gateway|interface"` — if the interface is Check Point's utun rather than the IMN tunnel's, it is the VPN clash. Ask
the user to disconnect Check Point; that is the clean fix. The alternative is
pinning routes narrower than the /17 onto the IMN utun with `sudo route add -net <subnet>/24 -interface <utun>`, which needs sudo and does not survive a
Check Point reconnect.

Separately, a station that IS reachable can still be flaky while RMS is
capturing (roughly 16:16 UTC to dawn) — timeouts and "Connection reset by
peer" under load. That is a busy Pi, not a routing problem, and it is a
reason not to start anything CPU-heavy like TrackStack during capture.

See [[station-fleet-rollout]].
