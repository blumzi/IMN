#!/usr/bin/python

""" Select each night's best-of-station bolides and publish their videos to
    the Israeli Astronomical Association YouTube channel.

    Called from IMN.py:rmsExternal() after RMS finishes processing a night.
    RMS is vendored third-party code and is never modified here -- we only
    import its Formats parsers and invoke its Utils (FRbinViewer) as a
    subprocess, locating the RMS tree at runtime via config.rms_root_dir.

    Selection (per station, no cross-station calibration):
      - keep meteors whose peak *apparent* magnitude <= mag_threshold (-1)
      - rank by saturated-frame fraction (primary), angular trail length (tie)
      - if no meteor in the night carries saturation data, fall back to
        ranking by peak brightness alone
      - take the best N (default 3)

    Video is produced by RMS Utils.FRbinViewer (mp4, headless -x), then handed
    to youtube.upload_video().

    Can also be run standalone for testing:
        python bolides.py <archived_night_dir> [--dry-run] [-n 3] [--mag -1]
"""

import os
import sys
import glob
import json
import logging
import subprocess

log = logging.getLogger("IMN.bolides")


# Column indices inside a meteor's per-frame measurement rows, as returned by
# RMS.Formats.FTPdetectinfo.readFTPdetectinfo (expanded format):
#   [calib_status, frame_n, x, y, ra, dec, azim, elev, inten, mag,
#    background, snr, saturated_count]
_RA, _DEC, _MAG, _SAT = 4, 5, 9, 12

DEFAULT_N = 3
DEFAULT_MAG_THRESHOLD = -1.0
STAGING_SUBDIR = "IMN_bolides"


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
    night_has_sat = False
    for meteor in meteors:
        peak_mag, sat_fraction, has_sat, trail_deg = _metrics(meteor)

        if peak_mag is None or peak_mag > mag_threshold:
            continue

        night_has_sat = night_has_sat or has_sat
        candidates.append(Bolide(meteor[0], peak_mag, sat_fraction, has_sat, trail_deg))

    if night_has_sat:
        # Primary: saturated-frame fraction (desc). Tiebreak: trail length (desc).
        candidates.sort(key=lambda c: (c.sat_fraction or 0.0, c.trail_deg), reverse=True)
    else:
        # Fallback for the whole night: brightest first, longer trail as tiebreak.
        candidates.sort(key=lambda c: (c.peak_mag, -c.trail_deg))

    selected = candidates[:n]
    log.info("selected %d/%d bolides (mag<=%.1f, %s ranking) from %s",
             len(selected), len(candidates), mag_threshold,
             "saturation" if night_has_sat else "brightness-fallback", archived_dir)
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


def render_bolide(archived_dir, bolide, config, staging_root):
    """ Render a single bolide to mp4(s) with RMS Utils.FRbinViewer.

        Only the selected FR (+ matching FF and the FTPdetectinfo, for the
        on-frame overlays) is staged into its own dir, so FRbinViewer renders
        just this bolide -- important on a resource-constrained Pi.
        Returns the list of produced mp4 paths.
    """
    from RMS.Formats.FTPdetectinfo import findFTPdetectinfoFile

    fr_name = _fr_for_ff(archived_dir, bolide.ff_name)
    if fr_name is None:
        log.warning("no FR file matching %s; skipping render", bolide.ff_name)
        return []
    bolide.fr_name = fr_name

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

    bolide.video_paths = sorted(glob.glob(os.path.join(stage, "*.mp4")))
    if not bolide.video_paths:
        log.warning("FRbinViewer produced no mp4 for %s", fr_name)
    return bolide.video_paths


def _title_and_description(bolide, config):
    """ Build a YouTube title/description from the bolide's FF datetime. """
    from RMS.Formats.FFfile import filenameToDatetime

    try:
        dt = filenameToDatetime(bolide.ff_name)
        when = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        when = bolide.ff_name

    title = "Bolide {} {}".format(config.stationID, when)
    description = (
        "Bolide captured by Israeli Meteor Network station {}.\n"
        "Time: {}\n"
        "Peak apparent magnitude: {:.1f}\n"
        "Angular trail length: {:.2f} deg\n"
        "Location: {:.5f} N, {:.5f} E, {:.1f} m\n"
    ).format(config.stationID, when, bolide.peak_mag, bolide.trail_deg,
             config.latitude, config.longitude, config.elevation)
    return title, description


def publish_bolides(archived_dir, config, n=DEFAULT_N,
                    mag_threshold=DEFAULT_MAG_THRESHOLD, dry_run=False):
    """ Full nightly flow: select -> render -> upload -> write manifest.

        Safe to call unconditionally: if no bolides qualify, or the YouTube
        credentials are absent, it logs and returns without raising. Never let
        a failure here break the RMS nightly flow -- rmsExternal wraps the call.
    """
    selected = select_bolides(archived_dir, mag_threshold=mag_threshold, n=n)
    if not selected:
        log.info("no qualifying bolides in %s", archived_dir)
        return []

    staging_root = os.path.join(archived_dir, STAGING_SUBDIR)
    if not os.path.isdir(staging_root):
        os.makedirs(staging_root)

    uploader = None
    if not dry_run:
        import youtube
        uploader = youtube.get_uploader()  # None if credentials are not present
        if uploader is None:
            log.info("YouTube credentials not configured; rendering only, no upload")

    for bolide in selected:
        render_bolide(archived_dir, bolide, config, staging_root)

        if uploader is None:
            continue

        title, description = _title_and_description(bolide, config)
        for video_path in bolide.video_paths:
            try:
                video_id = uploader.upload_video(video_path, title, description)
                bolide.video_ids.append(video_id)
                log.info("uploaded %s -> https://youtu.be/%s", video_path, video_id)
            except Exception as e:
                log.error("upload failed for %s: %r", video_path, e)

    manifest_path = os.path.join(staging_root, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump({"station": config.stationID,
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.dry_run:
        for i, b in enumerate(select_bolides(args.archived_dir, args.mag, args.n), 1):
            sat = "n/a" if b.sat_fraction is None else "{:.2f}".format(b.sat_fraction)
            print("{}. {}  peak_mag={:.1f}  sat_frac={}  trail={:.2f}deg".format(
                i, b.ff_name, b.peak_mag, sat, b.trail_deg))
        return

    from RMS.ConfigReader import loadConfigFromDirectory
    config = loadConfigFromDirectory(".", os.path.abspath("."))
    publish_bolides(args.archived_dir, config, n=args.n, mag_threshold=args.mag)


if __name__ == "__main__":
    _standalone()
