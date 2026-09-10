#!/usr/bin/python

""" Select each night's best-of-station bolides and publish their videos to
    the Israeli Astronomical Association YouTube channel.

    Called from IMN.py:rmsExternal() after RMS finishes processing a night.
    RMS is vendored third-party code and is never modified here -- we only
    import its Formats parsers and invoke its Utils (FRbinViewer) as a
    subprocess, locating the RMS tree at runtime via config.rms_root_dir.

    Selection (per station, no cross-station calibration):
      - keep meteors whose peak *apparent* magnitude <= mag_threshold (-1)
      - rank by saturated-frame fraction, then peak brightness, then angular
        trail length -- so a night whose saturation column is absent or all
        zero (the usual case) ranks by brightness on its own
      - take the best N (default 3)

    Video is produced by RMS Utils.FRbinViewer (mp4, headless -x) and handed to
    youtube.upload_video(). FRbinViewer is the preferred renderer -- it is what
    the station has always used, and it draws the frame number, timestamp and
    shower name onto the video. It needs RMS's Cython extensions, which are
    built on the station but do not compile everywhere (notably not on an Apple
    Silicon Mac, where RMS mistakes 'arm64' for a Raspberry Pi and picks the
    RPi's gcc-only flags). So if it fails we fall back to compositing the FR
    crops ourselves with the pure-Python RMS.Formats readers -- no overlays, but
    a usable clip on any dev machine.

    Can also be run standalone for testing:
        python bolides.py <archived_night_dir> [--dry-run] [--no-upload]
                                              [-n 3] [--mag -1]

    --dry-run prints the selection only; --no-upload also renders the mp4s but
    stops short of publishing them.
"""

import os
import sys
import glob
import json
import struct
import logging
import subprocess

import imnconfig

log = logging.getLogger("IMN.bolides")


# Column indices inside a meteor's per-frame measurement rows, as returned by
# RMS.Formats.FTPdetectinfo.readFTPdetectinfo (expanded format):
#   [calib_status, frame_n, x, y, ra, dec, azim, elev, inten, mag,
#    background, snr, saturated_count]
_RA, _DEC, _MAG, _SAT = 4, 5, 9, 12

STAGING_SUBDIR = "IMN_bolides"

# Tunables, from ~/.config/IMN/config.toml where it exists (see imnconfig.py and
# config.toml.example); the values below are the built-in defaults. Read once at
# import: the nightly hook is a fresh process each night, so editing the config
# takes effect on the next run without any reload machinery.
DEFAULT_N = imnconfig.get("bolides", "n")
DEFAULT_MAG_THRESHOLD = imnconfig.get("bolides", "mag_threshold")

# A bolide crosses the frame in well under a second, which is too quick to watch.
# Every clip is retimed before upload: played at 1/SLOWDOWN speed, then holding
# the last frame for HOLD_SECONDS so the trail stays on screen.
SLOWDOWN = imnconfig.get("bolides", "slowdown")
HOLD_SECONDS = imnconfig.get("bolides", "hold_seconds")


def _is_num(v):
    """ True for a real (non-NaN) number. NaN != NaN, so this filters NaNs. """
    return v is not None and v == v


class Bolide(object):
    """ One selected bolide and its computed selection metrics. """

    def __init__(self, ff_name, peak_mag, sat_fraction, has_sat, trail_deg):
        self.ff_name = ff_name
        self.peak_mag = peak_mag            # apparent, most negative frame
        self.sat_fraction = sat_fraction    # None if no saturation column
        self.has_sat = has_sat
        self.trail_deg = trail_deg
        self.fr_name = None                 # resolved at render time
        self.video_paths = []
        self.video_ids = []

    def as_dict(self):
        return {
            "ff_name": self.ff_name,
            "fr_name": self.fr_name,
            "peak_mag": self.peak_mag,
            "sat_fraction": self.sat_fraction,
            "trail_deg": self.trail_deg,
            "video_paths": self.video_paths,
            "video_ids": self.video_ids,
        }


def _metrics(meteor):
    """ Compute (peak_mag, sat_fraction, has_sat, trail_deg) for one meteor.

        peak_mag is the brightest (most negative) per-frame magnitude.
        sat_fraction is the fraction of frames with saturated pixels, or None
        if the FTPdetectinfo carries no saturation data for this meteor.
    """
    meas = meteor[11]

    mags = [row[_MAG] for row in meas if _is_num(row[_MAG])]
    peak_mag = min(mags) if mags else None

    sat_vals = [row[_SAT] for row in meas if _is_num(row[_SAT])]
    has_sat = len(sat_vals) > 0
    n_frames = len(meas)
    if has_sat and n_frames:
        sat_fraction = sum(1 for s in sat_vals if s > 0) / float(n_frames)
    else:
        sat_fraction = None

    pts = [(row[_RA], row[_DEC]) for row in meas
           if _is_num(row[_RA]) and _is_num(row[_DEC])]
    if len(pts) >= 2:
        # Reuse the vendored RMS helper rather than reimplementing it.
        from RMS.Math import angularSeparationDeg
        trail_deg = angularSeparationDeg(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1])
    else:
        trail_deg = 0.0

    return peak_mag, sat_fraction, has_sat, trail_deg


def select_bolides(archived_dir, mag_threshold=DEFAULT_MAG_THRESHOLD, n=DEFAULT_N):
    """ Read the night's FTPdetectinfo and return the best N bolides. """

    from RMS.Formats.FTPdetectinfo import findFTPdetectinfoFile, readFTPdetectinfo

    ftp_path = findFTPdetectinfoFile(archived_dir)
    meteors = readFTPdetectinfo(os.path.dirname(ftp_path), os.path.basename(ftp_path))

    candidates = []
    for meteor in meteors:
        peak_mag, sat_fraction, has_sat, trail_deg = _metrics(meteor)

        if peak_mag is None or peak_mag > mag_threshold:
            continue

        candidates.append(Bolide(meteor[0], peak_mag, sat_fraction, has_sat, trail_deg))

    # Saturation leads where it discriminates, brightness always breaks the tie,
    # trail length last. A night whose saturation column is absent *or* all-zero
    # (common: nothing saturates unless it is genuinely bright) therefore ranks
    # by brightness on its own, rather than degenerating to trail length.
    candidates.sort(key=lambda c: (c.sat_fraction or 0.0, -c.peak_mag, c.trail_deg),
                    reverse=True)

    selected = candidates[:n]
    night_has_sat = any(c.sat_fraction for c in candidates)
    log.info("selected %d/%d bolides (mag<=%.1f, %s ranking) from %s",
             len(selected), len(candidates), mag_threshold,
             "saturation" if night_has_sat else "brightness", archived_dir)
    return selected


def _fr_for_ff(archived_dir, ff_name):
    """ Find the FR_*.bin file matching an FF file, by shared datetime core.

        FF and FR share everything but the FF/FR prefix and the extension
        (FF is .fits/.bin, FR is always .bin) -- the same match RMS itself uses.
    """
    from RMS.Formats.FRbin import validFRName

    ff_core = os.path.splitext(ff_name)[0][2:]  # drop the 'FF' prefix
    for name in sorted(os.listdir(archived_dir)):
        if validFRName(name) and os.path.splitext(name)[0][2:] == ff_core:
            return name
    return None


def _link_into(src, dst):
    """ Symlink src -> dst, falling back to no-op if the link already exists. """
    if not os.path.exists(dst):
        os.symlink(src, dst)


def _render_with_frbinviewer(archived_dir, bolide, config, staging_root):
    """ Render with RMS Utils.FRbinViewer -- the station's renderer, which draws
        the frame number, timestamp and shower name onto the clip.

        Only the selected FR (+ matching FF and the FTPdetectinfo, for the
        on-frame overlays) is staged into its own dir, so FRbinViewer renders
        just this bolide -- important on a resource-constrained Pi.
        Returns the list of produced mp4 paths, empty if it failed.
    """
    from RMS.Formats.FTPdetectinfo import findFTPdetectinfoFile

    fr_name = bolide.fr_name
    stage = os.path.join(staging_root, os.path.splitext(fr_name)[0])
    if not os.path.isdir(stage):
        os.makedirs(stage)

    _link_into(os.path.join(archived_dir, fr_name), os.path.join(stage, fr_name))
    ff_path = os.path.join(archived_dir, bolide.ff_name)
    if os.path.exists(ff_path):
        _link_into(ff_path, os.path.join(stage, bolide.ff_name))
    ftp_path = findFTPdetectinfoFile(archived_dir)
    _link_into(ftp_path, os.path.join(stage, os.path.basename(ftp_path)))

    # Run FRbinViewer with the SAME interpreter (already the ~/vRMS venv, since
    # rmsExternal runs inside it) from the RMS root so `-m Utils.FRbinViewer`
    # resolves. -x is mandatory: without it OpenCV opens a window and dies
    # headless on the Pi.
    cmd = [sys.executable, "-m", "Utils.FRbinViewer",
           stage, "-e", "-f", "mp4", "-x", "-c", config.config_file_name]
    log.info("rendering %s: %s", fr_name, " ".join(cmd))
    subprocess.call(cmd, cwd=config.rms_root_dir)

    return sorted(glob.glob(os.path.join(stage, "*.mp4")))


class _FR(object):
    """ The fields of RMS's fr_struct that the renderer needs. """

    def __init__(self):
        self.lines = 0
        self.frameNum, self.yc, self.xc, self.t, self.size, self.frames = \
            [], [], [], [], [], []


def _read_fr(fr_path):
    """ Read an FR*.bin file: uint32 line count, then per line a uint32 frame
        count, then per frame yc/xc/t/size as uint32 followed by size*size uint8
        pixels.

        RMS.Formats.FRbin.read parses the same layout, but does it with
        int(np.fromfile(..., count=1)), which NumPy 2 refuses -- so it works on
        the station's NumPy 1.x and raises on a modern dev machine. Reading the
        handful of fields ourselves keeps the fallback working everywhere
        without touching vendored RMS.
    """
    import numpy as np

    fr = _FR()
    with open(fr_path, "rb") as fid:

        def _u32():
            return int(struct.unpack("I", fid.read(4))[0])

        fr.lines = _u32()
        for _ in range(fr.lines):
            frame_num = _u32()
            yc, xc, t, size, frames = [], [], [], [], []

            for _ in range(frame_num):
                yc.append(_u32())
                xc.append(_u32())
                t.append(_u32())
                size.append(_u32())
                count = size[-1]**2
                pixels = np.frombuffer(fid.read(count), dtype=np.uint8)
                frames.append(np.reshape(pixels, (size[-1], size[-1])))

            fr.frameNum.append(frame_num)
            fr.yc.append(yc)
            fr.xc.append(xc)
            fr.t.append(t)
            fr.size.append(size)
            fr.frames.append(frames)

    return fr


def _background_for(archived_dir, ff_name, fr):
    """ Return the image the FR crops are pasted onto: the FF maxpixel when the
        FF survived, else a black square just big enough to hold every crop
        (what FRbinViewer falls back to). Dimensions are made even, since
        H.264 requires it. """
    import numpy as np

    ff_path = os.path.join(archived_dir, ff_name)
    if os.path.exists(ff_path):
        from RMS.Formats import FFfile
        ff = FFfile.read(archived_dir, ff_name)
        if ff is not None:
            return np.copy(ff.maxpixel)

    extent = max(max(np.array(fr.yc[i]) + np.array(fr.size[i])//2,
                     np.array(fr.xc[i]) + np.array(fr.size[i])//2).max()
                 for i in range(fr.lines))
    size = int(extent) + (int(extent) % 2)
    return np.zeros((size, size), np.uint8)


def _composite_frames(fr, background):
    """ Yield the bolide's frames in time order, each crop pasted onto a copy of
        the background.

        This is FRbinViewer's default (non-split) compositing: every FR line is
        merged into one clip keyed by frame number, so a detection spanning
        several lines stays a single video. Reimplemented here because importing
        Utils.FRbinViewer pulls in RMS's Cython extensions, while the FR/FF
        format readers it relies on are pure Python.
    """
    import numpy as np

    clips = {}
    for line in range(fr.lines):
        for z in range(fr.frameNum[line]):
            clips.setdefault(fr.t[line][z], []).append((line, z))

    height, width = background.shape[:2]

    for t in sorted(clips):
        img = np.copy(background)

        for line, z in clips[t]:
            size = fr.size[line][z]
            y0 = fr.yc[line][z] - size//2
            x0 = fr.xc[line][z] - size//2
            crop = fr.frames[line][z][:size, :size]

            # A crop can hang off the edge of the frame; clip both sides
            # together so the paste stays aligned.
            dy0, dx0 = max(0, -y0), max(0, -x0)
            y0, x0 = max(0, y0), max(0, x0)
            dy1 = min(size - dy0, height - y0)
            dx1 = min(size - dx0, width - x0)
            if dy1 <= 0 or dx1 <= 0:
                continue

            img[y0:y0 + dy1, x0:x0 + dx1] = crop[dy0:dy0 + dy1, dx0:dx0 + dx1]

        yield img


def _encode_mp4(frames, mp4_path, fps, ffmpeg_binary):
    """ Encode grayscale frames to H.264 by piping raw video into ffmpeg -- no
        intermediate PNGs, which is what FRbinViewer writes and then deletes.
        Falls back to OpenCV's writer if ffmpeg is unavailable.
        Returns True if the file was written. """
    frames = list(frames)
    if not frames:
        return False

    height, width = frames[0].shape[:2]

    if ffmpeg_binary:
        cmd = [ffmpeg_binary, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "gray",
               "-s", "{}x{}".format(width, height), "-framerate", str(fps),
               "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", mp4_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for img in frames:
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
        if proc.wait() == 0:
            return True
        log.warning("ffmpeg failed for %s; falling back to OpenCV", mp4_path)

    import cv2
    writer = cv2.VideoWriter(mp4_path, cv2.VideoWriter_fourcc(*"mp4v"),
                             fps, (width, height))
    try:
        for img in frames:
            writer.write(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    finally:
        writer.release()
    return os.path.isfile(mp4_path) and os.path.getsize(mp4_path) > 0


def _render_directly(archived_dir, bolide, config, staging_root):
    """ Render without RMS Utils: paste the FR crops onto the FF maxpixel and
        encode. Used when FRbinViewer is unavailable (its Cython extensions do
        not build on every dev machine). No overlays, but a usable clip. """
    fr_name = bolide.fr_name
    fr = _read_fr(os.path.join(archived_dir, fr_name))
    background = _background_for(archived_dir, bolide.ff_name, fr)
    mp4_path = os.path.join(staging_root, os.path.splitext(fr_name)[0] + ".mp4")

    log.info("rendering %s -> %s", fr_name, mp4_path)
    ok = _encode_mp4(_composite_frames(fr, background), mp4_path,
                     config.fps, getattr(config, "ffmpeg_binary", "ffmpeg"))
    return [mp4_path] if ok else []


def _retime(mp4_path, config, slowdown=SLOWDOWN, hold=HOLD_SECONDS):
    """ Slow a clip down and hold its last frame, in place.

        Applied to whatever the renderer produced, so FRbinViewer's output on
        the station and our own fallback end up paced the same way. Deliberately
        a post-processing step: FRbinViewer encodes at config.fps internally and
        also derives its burnt-in timestamps from it, so retiming there would
        mean either editing vendored RMS or feeding it a config that lies.

        Leaves the original untouched if ffmpeg is missing or the filter fails.
    """
    ffmpeg_binary = getattr(config, "ffmpeg_binary", "ffmpeg")
    if not ffmpeg_binary:
        log.warning("no ffmpeg; leaving %s at original speed", mp4_path)
        return mp4_path

    tmp_path = mp4_path + ".retimed.mp4"
    vf = "setpts={:.3f}*PTS,tpad=stop_mode=clone:stop_duration={:.3f}".format(
        slowdown, hold)
    cmd = [ffmpeg_binary, "-y", "-hide_banner", "-loglevel", "error",
           "-i", mp4_path, "-filter:v", vf,
           "-r", str(config.fps), "-an",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", tmp_path]

    if subprocess.call(cmd) == 0 and os.path.getsize(tmp_path) > 0:
        os.replace(tmp_path, mp4_path)
        log.info("retimed %s (%.1fx slower, %.1fs hold)", mp4_path, slowdown, hold)
    else:
        log.warning("could not retime %s; uploading at original speed", mp4_path)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return mp4_path


def render_bolide(archived_dir, bolide, config, staging_root):
    """ Render a single bolide to mp4(s) and return the paths produced.

        FRbinViewer first, since that is what the station runs and it draws the
        overlays; our own compositing only if it produced nothing. Either way the
        result is retimed before it is returned.
    """
    fr_name = _fr_for_ff(archived_dir, bolide.ff_name)
    if fr_name is None:
        log.warning("no FR file matching %s; skipping render", bolide.ff_name)
        return []
    bolide.fr_name = fr_name

    if not os.path.isdir(staging_root):
        os.makedirs(staging_root)

    try:
        paths = _render_with_frbinviewer(archived_dir, bolide, config, staging_root)
    except Exception as e:
        log.warning("FRbinViewer unusable (%r)", e)
        paths = []

    if not paths:
        log.info("FRbinViewer produced no mp4 for %s; compositing directly", fr_name)
        try:
            paths = _render_directly(archived_dir, bolide, config, staging_root)
        except Exception as e:
            log.error("direct render failed for %s: %r", fr_name, e)
            paths = []

    if not paths:
        log.warning("produced no mp4 for %s", fr_name)

    paths = [_retime(path, config) for path in paths]

    bolide.video_paths = paths
    return paths


def _station_for(archived_dir, config):
    """ Return the station code this night belongs to.

        RMS names every file FF_<station>_<date>_..., so the archive itself says
        which camera recorded it. That is trusted over config.stationID: the
        config describes whichever station the process happens to be running as,
        which is not necessarily the one that recorded the night (reprocessing
        another station's data, or a stock RMS config whose stationID is still
        the default XX0001). Publishing a clip under the wrong station name is
        worse than the mismatch itself, so the data wins and the config is only
        a fallback.
    """
    codes = set()
    for name in os.listdir(archived_dir):
        if name.startswith(("FF_", "FR_")):
            parts = name.split("_")
            if len(parts) > 1 and parts[1]:
                codes.add(parts[1])

    if not codes:
        log.warning("no FF/FR files in %s; using config stationID %s",
                    archived_dir, config.stationID)
        return config.stationID

    if len(codes) > 1:
        log.warning("%s holds several station codes (%s); using config stationID %s",
                    archived_dir, ", ".join(sorted(codes)), config.stationID)
        return config.stationID

    station = codes.pop()
    if station != config.stationID:
        log.warning("night is from station %s but config says %s; using %s",
                    station, config.stationID, station)
    return station


def _title_and_description(bolide, config, station):
    """ Build a YouTube title/description from the bolide's FF datetime. """
    from RMS.Formats.FFfile import filenameToDatetime

    try:
        dt = filenameToDatetime(bolide.ff_name)
        when = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        when = bolide.ff_name

    title = "Bolide {} {}".format(station, when)
    description = (
        "Bolide captured by Israeli Meteor Network station {}.\n"
        "Time: {}\n"
        "Peak apparent magnitude: {:.1f}\n"
        "Angular trail length: {:.2f} deg\n"
        "Location: {:.5f} N, {:.5f} E, {:.1f} m\n"
    ).format(station, when, bolide.peak_mag, bolide.trail_deg,
             config.latitude, config.longitude, config.elevation)
    return title, description


def publish_bolides(archived_dir, config, n=DEFAULT_N,
                    mag_threshold=DEFAULT_MAG_THRESHOLD, no_upload=False):
    """ Full nightly flow: select -> render -> upload -> write manifest.

        no_upload renders and writes the manifest but never touches YouTube --
        the way to exercise the render path against a real night without
        publishing to the channel.

        Safe to call unconditionally: if no bolides qualify, or the YouTube
        credentials are absent, it logs and returns without raising. Never let
        a failure here break the RMS nightly flow -- rmsExternal wraps the call.
    """
    selected = select_bolides(archived_dir, mag_threshold=mag_threshold, n=n)
    if not selected:
        log.info("no qualifying bolides in %s", archived_dir)
        return []

    station = _station_for(archived_dir, config)

    staging_root = os.path.join(archived_dir, STAGING_SUBDIR)
    if not os.path.isdir(staging_root):
        os.makedirs(staging_root)

    uploader = None
    if not no_upload:
        import youtube
        uploader = youtube.get_uploader()  # None if credentials are not present
        if uploader is None:
            log.info("YouTube credentials not configured; rendering only, no upload")

    for bolide in selected:
        render_bolide(archived_dir, bolide, config, staging_root)

        if uploader is None:
            continue

        title, description = _title_and_description(bolide, config, station)
        for video_path in bolide.video_paths:
            try:
                video_id = uploader.upload_video(video_path, title, description,
                                                 station=station)
                bolide.video_ids.append(video_id)
                log.info("uploaded %s -> https://youtu.be/%s", video_path, video_id)
            except Exception as e:
                log.error("upload failed for %s: %r", video_path, e)

    manifest_path = os.path.join(staging_root, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump({"station": station,
                   "archived_dir": archived_dir,
                   "bolides": [b.as_dict() for b in selected]}, f, indent=2)
    log.info("wrote %s", manifest_path)
    return selected


def _standalone():
    """ CLI entry for testing selection/render on a night without RMS running. """
    import argparse

    parser = argparse.ArgumentParser(description="Select/render/publish night bolides.")
    parser.add_argument("archived_dir", help="Path to an archived night directory.")
    parser.add_argument("-n", type=int, default=DEFAULT_N, help="Max bolides to publish.")
    parser.add_argument("--mag", type=float, default=DEFAULT_MAG_THRESHOLD,
                        help="Peak apparent magnitude threshold (keep <=).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only print the selection; no render or upload.")
    parser.add_argument("--no-upload", action="store_true",
                        help="Select and render, but do not upload to YouTube.")
    parser.add_argument("-c", "--config", default=".",
                        help="Directory holding the RMS .config to use, or the "
                             "config file itself (default: current directory).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.dry_run:
        for i, b in enumerate(select_bolides(args.archived_dir, args.mag, args.n), 1):
            sat = "n/a" if b.sat_fraction is None else "{:.2f}".format(b.sat_fraction)
            print("{}. {}  peak_mag={:.1f}  sat_frac={}  trail={:.2f}deg".format(
                i, b.ff_name, b.peak_mag, sat, b.trail_deg))
        return

    from RMS.ConfigReader import loadConfigFromDirectory

    # loadConfigFromDirectory takes the config's *directory*; accept a path to
    # the file itself too, since that is the obvious thing to pass.
    config_dir = args.config
    if os.path.isfile(config_dir):
        config_dir = os.path.dirname(os.path.abspath(config_dir))
    config = loadConfigFromDirectory(".", os.path.abspath(config_dir))
    publish_bolides(args.archived_dir, config, n=args.n, mag_threshold=args.mag,
                    no_upload=args.no_upload)


if __name__ == "__main__":
    _standalone()
