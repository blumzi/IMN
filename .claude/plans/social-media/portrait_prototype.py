""" Prototype (see ../social-media.md): turn a 16:9 bolide clip into a
    1080x1350 (4:5) clip for Facebook/Instagram/TikTok.

    Layout, top to bottom: English title; the video; a data block where each
    row has its label stacked in English, Hebrew, Arabic and Russian beside a
    single English value; the network name and station code.

    Text is drawn by Pillow into a transparent PNG and overlaid, so ffmpeg
    needs no drawtext/freetype. Hebrew/Arabic need Pillow with libraqm.
    Fonts are macOS paths -- the station version needs fonts it actually has.
    Values are hard-coded for the IL0003 2026-08-18 sample.

    usage: python portrait_prototype.py <in.mp4> <out.mp4> <overlay.png>
"""
import subprocess, sys
from PIL import Image, ImageDraw, ImageFont

src, out, png = sys.argv[1], sys.argv[2], sys.argv[3]
W, H = 1080, 1350             # 4:5; full-screen players letterbox it, so
                              # their buttons land outside our content
VH = 608                      # 1280x720 scaled to width 1080
VY = 250                      # video top edge
FPS = 25
EXTRA_HOLD = 2.5              # on top of the 1.5 s _retime hold -> 4 s total

HELV = "/System/Library/Fonts/Helvetica.ttc"
UNI = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
helv = lambda s, bold=False: ImageFont.truetype(HELV, s, index=1 if bold else 0)
uni = lambda s: ImageFont.truetype(UNI, s)

WHITE = (255, 255, 255, 255)
GREY = (215, 215, 215, 255)
QUIET = (150, 150, 150, 255)
RULE = (70, 70, 70, 255)

img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

def text(x, y, s, f, fill, anchor="la", direction=None, language=None):
    d.text((x, y), s, font=f, fill=fill, anchor=anchor,
           direction=direction, language=language)

# title, English only
text(W / 2, 150, "Bright meteor over Israel", helv(70, True), WHITE, anchor="ms")

# data block: (labels en/he/ar/ru, value)
ROWS = [
    (("Date · Israel time",
      "תאריך · שעון ישראל",
      "التاريخ · توقيت إسرائيل",
      "Дата · время в Израиле"),
     "18 Aug 2026 · 21:52"),
    (("Peak brightness · Trail length",
      "בהירות מרבית · אורך השובל",
      "أقصى سطوع · طول الأثر",
      "Макс. яркость · Длина следа"),
     "magnitude −1.4 · 10.3°"),
]
LANG = [(None, None), ("rtl", "he"), ("rtl", "ar"), (None, "ru")]
LABEL_X = 450                 # labels right-aligned here
VALUE_X = 490                 # values left-aligned here
LINE = 31                     # label line spacing
ROW = 4 * LINE + 22           # row height

y = VY + VH + 40
for i, (labels, value) in enumerate(ROWS):
    if i:
        d.line([(80, y - 11), (W - 80, y - 11)], fill=RULE, width=1)
    for j, (label, (direction, language)) in enumerate(zip(labels, LANG)):
        fill = GREY if j == 0 else QUIET
        text(LABEL_X, y + 26 + j * LINE, label, uni(25), fill, anchor="rs",
             direction=direction, language=language)
    text(VALUE_X, y + 2 * LINE + 26, value, helv(44, True), WHITE, anchor="lm")
    y += ROW

# footer, English
text(W / 2, y + 50, "Israeli Meteor Network · IL0003", helv(36), QUIET, anchor="ms")
img.save(png)

frames = int(subprocess.check_output(
    ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
     "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", src]).strip())
hold = max(1, int(round(EXTRA_HOLD * FPS)))

# loop (not tpad) so this also runs on the stations' ffmpeg 4.1.4
fc = ("[0:v]loop=loop={h}:size=1:start={s},setpts=N/{fps}/TB,"
      "scale={W}:{VH},pad={W}:{H}:0:{VY}:black[v];"
      "[v][1:v]overlay=0:0,format=yuv420p[o]").format(
          h=hold, s=frames - 1, fps=FPS, W=W, H=H, VH=VH, VY=VY)
subprocess.check_call(["ffmpeg", "-v", "error", "-y", "-i", src, "-i", png,
                       "-filter_complex", fc, "-map", "[o]", "-r", str(FPS),
                       "-c:v", "libx264", "-profile:v", "high", "-crf", "20",
                       "-movflags", "+faststart", "-an", out])
print("ok", out)
