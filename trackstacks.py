#!/usr/bin/python

""" Per-station cumulative shower track-stack videos.

    During a shower, each station keeps ONE cumulative video per shower on the
    association's YouTube channel, showing its track-stacked frames for every
    night of the shower so far. It is rebuilt and republished each morning.

    Design and rationale: plans/shower-trackstack-video.md. In short:

      1. A shower is active if THIS station associated at least min_meteors to
         it last night. Nothing reads a calendar; the station's own detections
         decide both when a shower starts and when it is over.
      2. RMS Utils/TrackStack renders that night's stack, as a subprocess under
         a wall-clock timeout -- it re-reads every FF file in the night and runs
         inside rmsExternal, which holds the reboot lock RMS only ever waits on.
      3. The JPEG is archived outside RMS_data, where RMS's quota-driven cleanup
         cannot reap it and where it does not count against that quota.
      4. The cumulative video is rebuilt from the archive with ffmpeg.
      5. It is published, and an older generation retired -- one cycle late, so
         a still-transcoding upload never leaves the playlist unwatchable.

    Everything is guarded: this runs at the end of a night's processing and must
    never be the reason a station fails to finish or reboot.

    Off unless [trackstack] enabled = true in config.toml.
"""

import os
import json
import glob
import time
import shutil
import logging
import tempfile
import subprocess

import imnconfig

log = logging.getLogger("IMN.trackstacks")


def _path(key):
    return os.path.expanduser(imnconfig.get("trackstack", key))


def analyse_night(archived_dir, config):
    """ Associate last night's meteors with showers.

        Returns (ranked, ff_by_shower):
          ranked       -- [(code, count)] over min_meteors, best represented first
          ff_by_shower -- {code: sorted [FF file names] containing its meteors}

        The FF mapping is why we do the association ourselves rather than
        letting TrackStack filter: see _stage_shower().
    """
    from Utils.ShowerAssociation import showerAssociation
    from RMS.Formats.FTPdetectinfo import findFTPdetectinfoFile

    ftp_path = findFTPdetectinfoFile(archived_dir)
    if not ftp_path:
        log.warning("no FTPdetectinfo in %s; no showers", archived_dir)
        return [], {}

    associations, shower_counts = showerAssociation(
        config, [ftp_path], show_plot=False, save_plot=False)

    # associations maps (ff_name, meteor_number) -> (meteor, shower). Every
    # meteor counts, whatever its number within the FF.
    ff_by_shower = {}
    for (ff_name, _meteor_no), (_meteor, shower) in associations.items():
        if shower is not None:
            ff_by_shower.setdefault(shower.name, set()).add(os.path.basename(ff_name))

    minimum = imnconfig.get("trackstack", "min_meteors")
    ranked = [(shower.name, count) for shower, count in shower_counts
              if shower is not None and count >= minimum]

    log.info("active showers in %s: %s", archived_dir,
             ", ".join("{}({})".format(c, n) for c, n in ranked) or "none")

    return ranked, {code: sorted(ffs) for code, ffs in ff_by_shower.items()}


def active_showers(archived_dir, config):
    """ Just the ranked [(code, count)], for callers that do not need the FFs. """
    ranked, _ff_by_shower = analyse_night(archived_dir, config)
    return ranked


def _stage_shower(archived_dir, code, ff_names, stage_root, config):
    """ Build a directory holding only one shower's FF files, for TrackStack.

        TrackStack's own -s/--showers filter cannot be used: shouldInclude()
        looks the FF up as associations[(ff_name, 1.0)], hardcoding meteor
        number 1, so an FF whose shower meteor is not the first detection is
        silently dropped -- and a bare except turns every miss into a quiet
        False. On a real night that left nothing to stack and produced a
        uniformly white image. RMS is vendored and not ours to fix.

        Unfiltered, TrackStack takes its FF list from os.listdir() intersected
        with the recalibrated platepars, so a directory containing just this
        shower's FFs stacks exactly them. Same staging trick bolides.py uses
        for FRbinViewer.

        Returns the staging directory, or None if there is too little to stack.
    """
    # TrackStack needs at least two FFs to establish a reference frame.
    if len(ff_names) < 2:
        log.info("%s has only %d FF file(s); too few to stack", code, len(ff_names))
        return None

    stage = os.path.join(stage_root,
                         os.path.basename(archived_dir.rstrip(os.sep)) + "_" + code)
    if not os.path.isdir(stage):
        os.makedirs(stage)

    linked = 0
    for ff_name in ff_names:
        source = os.path.join(archived_dir, ff_name)
        if not os.path.isfile(source):
            continue
        target = os.path.join(stage, ff_name)
        if not os.path.exists(target):
            os.symlink(source, target)      # symlink: an FF is several MB
        linked += 1

    if linked < 2:
        log.warning("%s: only %d FF file(s) present on disk; not stacking", code, linked)
        return None

    # The platepars are what TrackStack aligns against, and it reads them from
    # the directory it is pointed at. A real copy, not a link: it is small, and
    # some RMS paths rewrite it.
    platepars = os.path.join(archived_dir, config.platepars_recalibrated_name)
    if not os.path.isfile(platepars):
        log.error("no %s in %s; cannot stack",
                  config.platepars_recalibrated_name, archived_dir)
        return None
    shutil.copy2(platepars, os.path.join(stage, config.platepars_recalibrated_name))

    log.info("staged %d FF file(s) for %s", linked, code)
    return stage


def _looks_blank(image_path):
    """ True if the image carries no detail.

        TrackStack can exit successfully having plotted nothing, leaving a
        uniformly white canvas. Without this a blank night would be archived
        and then baked into every rebuild from then on.
    """
    try:
        import cv2
        image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            return True
        return float(image.std()) < 1.0
    except Exception as e:
        log.warning("could not inspect %s (%r); assuming it is fine", image_path, e)
        return False


def make_stack(archived_dir, code, config, out_dir, timeout, ff_names, stage_root,
               caption=""):
    """ Render one shower's track stack, returning the image path or None.

        Stacks a staged directory of just this shower's FF files rather than
        passing -s: see _stage_shower() for why TrackStack's own filter is
        unusable.

        The work happens in trackstack_runner.py, as a subprocess. A subprocess
        because only that makes the wall-clock bound enforceable and contains a
        crash in a C extension; a runner of our own because RMS cannot save the
        stack it computes on these stations -- see that module.
    """
    import sys

    stage = _stage_shower(archived_dir, code, ff_names, stage_root, config)
    if stage is None:
        return None

    # The staging directory is already named <night>_<CODE>, so its basename is
    # the distinguishing part; appending the code again just doubles it.
    out_path = os.path.join(out_dir, os.path.basename(stage) + ".jpg")
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "trackstack_runner.py")

    cmd = [sys.executable, runner, stage, out_path,
           "--rms-root", config.rms_root_dir,
           "--caption", caption]

    log.info("stacking %s (timeout %ds)", code, timeout)
    started = time.time()

    try:
        status = subprocess.call(cmd, cwd=config.rms_root_dir, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.error("TrackStack for %s exceeded %ds; skipping this night",
                  code, timeout)
        return None
    except Exception as e:
        log.error("TrackStack for %s failed: %r", code, e)
        return None

    if status != 0 or not os.path.isfile(out_path):
        log.error("TrackStack for %s produced no image (status %s)", code, status)
        return None

    # The runner checks this too; repeated here because a blank frame archived
    # once is baked into every rebuild from then on.
    if _looks_blank(out_path):
        log.error("TrackStack for %s produced a blank image; discarding", code)
        os.remove(out_path)
        return None

    log.info("stacked %s in %ds", code, int(time.time() - started))
    return out_path


def archive_stack(image_path, station, code, night):
    """ File the night's stack where the cumulative video is built from.

        Named <STATION>_<YYYYMMDD>_<SHOWER>.jpg because the video is assembled
        by lexicographic glob, and the date in that position makes lexicographic
        order chronological order.
    """
    year = night[:4]
    dest_dir = os.path.join(_path("stacks_dir"), year, code)
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)

    dest = os.path.join(dest_dir, "{}_{}_{}.jpg".format(station, night, code))
    shutil.copy2(image_path, dest)
    log.info("archived %s", dest)
    return dest


def stack_paths(station, code, year):
    """ Every archived stack for one station/shower/year, chronologically. """
    pattern = os.path.join(_path("stacks_dir"), year, code,
                           "{}_*_{}.jpg".format(station, code))
    return sorted(glob.glob(pattern))


def build_video(station, code, year, out_path):
    """ Assemble the cumulative video from the archive. Returns out_path, or
        None if there was nothing to build or ffmpeg failed.

        Frames are fed by an explicit concat list rather than a glob: ffmpeg's
        glob pattern support is a build-time option and silently absent on some
        builds, and an explicit list also lets the last frame be held.
    """
    images = stack_paths(station, code, year)
    if not images:
        log.warning("no archived stacks for %s/%s/%s", station, code, year)
        return None

    seconds = imnconfig.get("trackstack", "framerate")

    # concat demuxer: each entry is a file and how long to show it. The last
    # image needs repeating -- the demuxer ignores the final duration.
    listing = []
    for image in images:
        listing.append("file '{}'\nduration {}".format(image, seconds))
    listing.append("file '{}'".format(images[-1]))

    handle, list_path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(handle, "w") as f:
        f.write("\n".join(listing) + "\n")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", list_path,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
           "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
                  "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
           out_path]

    try:
        status = subprocess.call(cmd)
    finally:
        os.remove(list_path)

    if status != 0 or not os.path.isfile(out_path):
        log.error("ffmpeg failed to build %s", out_path)
        return None

    log.info("built %s from %d night(s)", out_path, len(images))
    return out_path


# ---------------------------------------------------------------- state file

def load_state():
    path = _path("state_file")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except ValueError as e:
        log.error("%s is not valid JSON (%s); starting empty", path, e)
        return {}


def save_state(state):
    """ Write atomically. A truncated state file would orphan every live video
        it was tracking, and there is no cheap way to rediscover them. """
    path = _path("state_file")
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)

    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.rename(tmp, path)


def state_key(station, code, year):
    return "{}/{}/{}".format(station, code, year)


# ------------------------------------------------------------- orchestration

def _night_of(archived_dir):
    """ The night's date as YYYYMMDD, from the archive directory name.

        RMS names these <STATION>_<YYYYMMDD>_<HHMMSS>_<us>, so the date is the
        second field. Returns None if the name does not look like that.
    """
    parts = os.path.basename(archived_dir.rstrip(os.sep)).split("_")
    if len(parts) > 1 and len(parts[1]) == 8 and parts[1].isdigit():
        return parts[1]
    log.warning("cannot read a night date from %s", archived_dir)
    return None


def _title(station, code, year, images, meteors):
    """ Put currency in the title: how much is covered, and how recent. """
    nights = len(images)
    first = os.path.basename(images[0]).split("_")[1]
    last = os.path.basename(images[-1]).split("_")[1]
    return "{} · {} {} · {}–{} · {} night{} · {} meteors".format(
        station, code, year, first, last, nights, "" if nights == 1 else "s",
        meteors)


def _description(station, code, year, images, meteors):
    return (
        "Cumulative track stack of {} meteors from the {} shower, {}, "
        "captured by Israeli Meteor Network station {}.\n"
        "One frame per night, {} night(s) so far.\n"
        "Rebuilt each morning for as long as the shower stays active."
    ).format(meteors, code, year, station, len(images))


def _retire_old(uploader, entry, keep):
    """ Drop generations past `keep`, oldest first. Best effort: a video that
        cannot be deleted is dropped from state anyway, since retrying forever
        would pin the list at its maximum and never publish again. """
    generations = entry.get("generations", [])
    while len(generations) > keep:
        old = generations.pop()
        if uploader is not None:
            uploader.retire_video(old.get("video_id"),
                                  old.get("playlist_item_ids", []))
        else:
            log.info("would retire %s", old.get("video_id"))


def publish_shower_stacks(archived_dir, config, no_upload=False):
    """ The whole nightly flow. Returns the shower codes it published.

        Safe to call unconditionally: disabled by default, and every step is
        guarded so a failure here never breaks the night or the reboot lock.
    """
    if not imnconfig.get("trackstack", "enabled"):
        log.info("track stacks disabled; set [trackstack] enabled = true to turn on")
        return []

    import bolides

    night = _night_of(archived_dir)
    if night is None:
        return []

    station = bolides._station_for(archived_dir, config)
    year = night[:4]
    state = load_state()

    showers, ff_by_shower = analyse_night(archived_dir, config)
    seen = set()

    # Stack tonight's showers, best represented first, until the budget is out.
    # Ranked order means the quietest shower is the one dropped, not a random one.
    budget = imnconfig.get("trackstack", "total_timeout")
    per_shower = imnconfig.get("trackstack", "timeout")
    tmp_dir = tempfile.mkdtemp(prefix="imn-stack-")

    try:
        for code, count in showers:
            if budget <= 0:
                log.warning("nightly stack budget spent; %s and any after it skipped", code)
                break

            started = time.time()
            caption = "{}  {}-{}-{}  {}: {} meteors".format(
                station, night[:4], night[4:6], night[6:8], code, count)
            image = make_stack(archived_dir, code, config, tmp_dir,
                               int(min(per_shower, budget)),
                               ff_by_shower.get(code, []), tmp_dir, caption)
            budget -= time.time() - started
            if image is None:
                continue

            archive_stack(image, station, code, night)
            seen.add(code)

            key = state_key(station, code, year)
            entry = state.setdefault(key, {"generations": [], "meteors": 0})
            entry["meteors"] = entry.get("meteors", 0) + count
            entry["last_night"] = night
            entry["misses"] = 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # A shower already being tracked but not seen tonight is one night closer to
    # being over. Nothing consults a calendar: the station decides a shower has
    # ended the same way it decided it had started.
    grace = imnconfig.get("trackstack", "grace_nights")
    for key, entry in list(state.items()):
        entry_station, entry_code, entry_year = key.split("/")
        if entry_station != station or entry_code in seen:
            continue
        entry["misses"] = entry.get("misses", 0) + 1
        if entry["misses"] >= grace:
            log.info("%s over for %s after %d quiet night(s); leaving its video up",
                     entry_code, station, entry["misses"])
            del state[key]

    if not seen:
        save_state(state)
        return []

    uploader = None
    if not no_upload:
        import youtube
        uploader = youtube.get_uploader()
        if uploader is None:
            log.info("YouTube credentials not configured; stacks archived, not published")

    keep = imnconfig.get("trackstack", "generations")
    published = []

    for code in sorted(seen):
        key = state_key(station, code, year)
        entry = state[key]
        images = stack_paths(station, code, year)

        out_path = os.path.join(tempfile.gettempdir(),
                                "{}_{}_{}.mp4".format(station, code, year))
        if build_video(station, code, year, out_path) is None:
            continue

        try:
            if uploader is not None:
                title = _title(station, code, year, images, entry["meteors"])
                description = _description(station, code, year, images, entry["meteors"])
                video_id, item_ids = uploader.publish_video(
                    out_path, title, description,
                    tags=["meteor", "meteor shower", code, "IMN", station],
                    station=station)
                log.info("published %s %s -> https://youtu.be/%s", station, code, video_id)

                entry.setdefault("generations", []).insert(
                    0, {"video_id": video_id, "playlist_item_ids": item_ids,
                        "night": night})
                published.append(code)

                # Persist BEFORE retiring: a crash here orphans a video, which
                # is visible in the playlist and can be cleaned up by hand. The
                # other order risks losing the id of a LIVE video, and finding
                # it again would need search.list at 100 units against a
                # 100/day cap.
                save_state(state)

            _retire_old(uploader, entry, keep)
        except Exception as e:
            log.error("publishing %s for %s failed: %r", code, station, e)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    save_state(state)
    return published
