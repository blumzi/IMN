---
name: rms-is-vendored
description: "RMS is vendored/third-party code — never modify it, only import/invoke"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 7bf81146-7be3-4447-bfbe-f7f5ba604e20
---

RMS (CroatianMeteorNetwork/RMS, cloned at ~/src/RMS on this machine) is
VENDORED third-party code from the station's perspective. Do NOT edit RMS
sources for IMN features.

**Why:** stations pull RMS from upstream; local RMS edits would be
clobbered on update and diverge from the network.

**How to apply:** build IMN features in the IMN repo. Reuse RMS by
importing its modules (e.g. RMS.Formats.*) or invoking its Utils as
subprocesses (e.g. `python -m Utils.FRbinViewer ... -x`). Locate RMS at
runtime via `config.rms_root_dir`. See [[bolide-video-publishing]] and
[[imn-station-architecture]].
