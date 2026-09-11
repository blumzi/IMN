#!/usr/bin/python

""" Render one track stack, writing the image ourselves.

    Run as a subprocess by trackstacks.make_stack(), never imported.

    RMS computes the stack correctly but cannot save it here: its
    Utils/TrackStack hands a good array to matplotlib and savefig writes a
    uniformly white JPEG (agg backend, Python 3.7, matplotlib as pinned on the
    stations). Measured on IL0003: array mean 49.74 / std 20.84, saved file mean
    255.00 / std 0.00, while cv2.imwrite of that same array is perfect.

    So we let RMS do the astrometry and stacking, intercept the finished array
    on its way to the plot, and write it with OpenCV. RMS itself is untouched --
    the patch lives in this process only, and is undone straight after.

    That does couple us to an implementation detail: trackStack() calls
    plt.imshow() once with the completed 2D stack. If upstream restructures the
    plotting we get no image, which the caller treats as a failed stack. The
    caption is drawn here too, replacing TrackStack's textoption=2, which is
    matplotlib-drawn and would be lost with the rest of the figure.

    Usage:
        trackstack_runner.py <stage_dir> <out_path> --rms-root DIR [--caption S]
"""

from __future__ import print_function

import os
import sys
import argparse


def main():
    parser = argparse.ArgumentParser(description="Render one RMS track stack.")
    parser.add_argument("stage_dir", help="Directory of FF files to stack.")
    parser.add_argument("out_path", help="Where to write the JPEG.")
    parser.add_argument("--rms-root", required=True, help="RMS installation root.")
    parser.add_argument("--caption", default="", help="Text to draw along the bottom.")
    args = parser.parse_args()

    # RMS is not installed as a package; put its root on the path and run from
    # there, as its own entry points do.
    sys.path.insert(0, args.rms_root)
    os.chdir(args.rms_root)

    import numpy as np
    import cv2
    import Utils.TrackStack as TS
    from RMS.ConfigReader import loadConfigFromDirectory

    config = loadConfigFromDirectory(".", args.rms_root)

    captured = {}
    original_imshow = TS.plt.imshow

    def capturing_imshow(image, *args_, **kwargs):
        """ Keep the first 2D array plotted: that is the finished stack.
            Constellation overlays, when enabled, come through as RGB and are
            ignored. """
        data = np.asarray(image)
        if data.ndim == 2 and "stack" not in captured:
            captured["stack"] = data.copy()
        return original_imshow(image, *args_, **kwargs)

    TS.plt.imshow = capturing_imshow
    try:
        TS.trackStack([args.stage_dir], config, hide_plot=True, one_core_free=True)
    finally:
        TS.plt.imshow = original_imshow

    if "stack" not in captured:
        print("trackstack_runner: RMS plotted no stack", file=sys.stderr)
        return 1

    image = captured["stack"]
    if image.std() < 1.0:
        print("trackstack_runner: stack is blank", file=sys.stderr)
        return 1

    if args.caption:
        image = _add_caption(image, args.caption, cv2, np)

    out_dir = os.path.dirname(args.out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    if not cv2.imwrite(args.out_path, image):
        print("trackstack_runner: could not write %s" % args.out_path, file=sys.stderr)
        return 1

    print("trackstack_runner: wrote %s %s" % (args.out_path, image.shape))
    return 0


def _add_caption(image, caption, cv2, np):
    """ Add a caption band under the stack, the way TrackStack's textoption=2
        would have. Drawn on extra rows rather than over the sky, so nothing is
        hidden. """
    band = 34
    canvas = np.zeros((image.shape[0] + band, image.shape[1]), dtype=image.dtype)
    canvas[:image.shape[0], :] = image

    scale = max(0.4, min(0.8, image.shape[1] / 1600.0))
    cv2.putText(canvas, caption, (10, image.shape[0] + band - 11),
                cv2.FONT_HERSHEY_SIMPLEX, scale, 200, 1, cv2.LINE_AA)
    return canvas


if __name__ == "__main__":
    sys.exit(main())
