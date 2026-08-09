#!/usr/bin/env python3
"""
Burns a title + body text block onto a template video and writes an
Instagram-ready 1080x1920 MP4.

Text is drawn once to a transparent PNG with Pillow -- full control over
wrapping, centring, soft shadow and inline emoji -- then overlaid with ffmpeg.
That beats ffmpeg's own drawtext filter, which cannot wrap, auto-fit or do
emoji.

Layout defaults match the reference post: block anchored to the TOP of the
frame (not vertically centred), title high, a wide gap, then the body.

Usage:
    python3 render.py --template templates/baku-night.mp4 \
        --title "How to (Accidentally) Meet Your Rich 🚩 Spouse in Baku." \
        --body "$(cat tip1.txt)" --out videos/baku-01.mp4 --preview

Tuning:
    --title-top 0.135   title block start, as a fraction of frame height
    --body-top 0.325    body block start
    --body-end 0.70     body must finish above this
    --body-size 34      force a size (default: auto-fit to the space)
    --margin 115        side padding, px
    --style shadow      "shadow" (soft, IG-like) or "stroke" (hard outline)
    --scrim 0.35        0-1 darkening of the footage so text reads
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FPS = 30

TITLE_TOP = 0.135
BODY_TOP = 0.325
BODY_END = 0.70
MARGIN_X = 115

TITLE_MAX_SIZE, TITLE_MIN_SIZE = 58, 32
BODY_MAX_SIZE, BODY_MIN_SIZE = 44, 22

WHITE = (255, 255, 255, 255)
SHADOW_BLUR = 7
SHADOW_ALPHA = 190
STROKE_FILL = (0, 0, 0, 235)

CHARS_PER_SEC = 26
MIN_DURATION, MAX_DURATION = 12.0, 60.0

EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
EMOJI_NATIVE = 160          # the only size Apple Color Emoji loads cleanly

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]

_EMOJI_RANGES = [
    (0x1F300, 0x1FAFF), (0x1F000, 0x1F2FF), (0x2600, 0x27BF),
    (0x2B00, 0x2BFF), (0x1F1E6, 0x1F1FF),
]
_SKIP = {0xFE0E, 0xFE0F, 0x200D}     # variation selectors + zero-width joiner


def pick_font_path():
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("No bold font found. Edit FONT_CANDIDATES in render.py.")


FONT_PATH = pick_font_path()
_font_cache, _emoji_cache = {}, {}


def font(size):
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(FONT_PATH, size)
    return _font_cache[size]


def emoji_font():
    if "f" not in _emoji_cache:
        try:
            _emoji_cache["f"] = ImageFont.truetype(EMOJI_FONT, EMOJI_NATIVE)
        except Exception:
            _emoji_cache["f"] = None
    return _emoji_cache["f"]


def is_emoji(ch):
    cp = ord(ch)
    if cp in _SKIP:
        return False
    return any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


def segment(text):
    """Split into ('t', run) text runs and ('e', char) emoji characters."""
    out, buf = [], ""
    for ch in text:
        if ord(ch) in _SKIP:
            continue
        if is_emoji(ch):
            if buf:
                out.append(("t", buf))
                buf = ""
            out.append(("e", ch))
        else:
            buf += ch
    if buf:
        out.append(("t", buf))
    return out


def measure(draw, text, f):
    total = 0.0
    for kind, run in segment(text):
        total += draw.textlength(run, font=f) if kind == "t" else f.size * 1.20
    return total


def line_height(f):
    ascent, descent = f.getmetrics()
    return int((ascent + descent) * 1.18)


def draw_mixed(img, draw, x, y, text, f, stroke_w, stroke_fill):
    ef = emoji_font()
    for kind, run in segment(text):
        if kind == "t":
            draw.text((x, y), run, font=f, fill=WHITE,
                      stroke_width=stroke_w, stroke_fill=stroke_fill)
            x += draw.textlength(run, font=f)
        else:
            box = int(f.size * 1.20)
            if ef is not None:
                tile = Image.new("RGBA", (EMOJI_NATIVE * 2, EMOJI_NATIVE * 2), (0, 0, 0, 0))
                ImageDraw.Draw(tile).text((0, 0), run, font=ef, embedded_color=True)
                tile = tile.crop(tile.getbbox() or (0, 0, 1, 1))
                tile = tile.resize((box, max(1, int(box * tile.height / tile.width))),
                                   Image.LANCZOS)
                img.alpha_composite(tile, (int(x), int(y + f.size * 0.16)))
            x += box


def wrap(draw, text, f, max_width):
    lines = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        current = []
        for word in para.split():
            trial = current + [word]
            if measure(draw, " ".join(trial), f) <= max_width or not current:
                current = trial
            else:
                lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))
    return lines


def fit_block(draw, text, max_width, max_height, hi, lo, forced=None):
    """Largest font size at which the wrapped text fits the box."""
    if forced:
        f = font(forced)
        lines = wrap(draw, text, f, max_width)
        return f, lines, line_height(f) * len(lines)
    best = None
    while hi >= lo:
        mid = (hi + lo) // 2
        f = font(mid)
        lines = wrap(draw, text, f, max_width)
        h = line_height(f) * len(lines)
        if h <= max_height:
            best = (f, lines, h)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        f = font(lo)
        lines = wrap(draw, text, f, max_width)
        best = (f, lines, line_height(f) * len(lines))
    return best


def draw_block(img, draw, lines, f, top, stroke_w, stroke_fill):
    y, lh = top, line_height(f)
    for ln in lines:
        if ln:
            tw = measure(draw, ln, f)
            draw_mixed(img, draw, (W - tw) / 2, y, ln, f, stroke_w, stroke_fill)
        y += lh
    return y


def render_overlay(title, body, out_png, opts):
    max_width = W - 2 * opts["margin"]
    title_top = int(opts["title_top"] * H)
    body_top = int(opts["body_top"] * H)
    body_end = int(opts["body_end"] * H)
    hard = opts["style"] == "stroke"

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    title_f, title_lines, title_h = None, [], 0
    if title.strip():
        title_f, title_lines, title_h = fit_block(
            draw, title, max_width, max(1, body_top - title_top - 20),
            TITLE_MAX_SIZE, TITLE_MIN_SIZE, opts["title_size"])

    body_f, body_lines, body_h = fit_block(
        draw, body, max_width, max(1, body_end - body_top),
        BODY_MAX_SIZE, BODY_MIN_SIZE, opts["body_size"])

    if title_h:
        draw_block(img, draw, title_lines, title_f, title_top,
                   max(2, title_f.size // 14) if hard else 0, STROKE_FILL)
    draw_block(img, draw, body_lines, body_f, body_top,
               max(2, body_f.size // 15) if hard else 0, STROKE_FILL)

    if not hard:
        # Soft drop shadow: a blurred black silhouette composited beneath.
        alpha = img.split()[3]
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        shadow.putalpha(alpha.point(lambda v: int(v * SHADOW_ALPHA / 255)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
        img = Image.alpha_composite(shadow, img)

    img.save(out_png)
    return {"title_size": title_f.size if title_f else None,
            "body_size": body_f.size, "body_lines": len(body_lines),
            "body_bottom": round((body_top + body_h) / H, 3)}


def probe_has_audio(ffmpeg, path):
    return "Audio:" in subprocess.run([ffmpeg, "-i", path],
                                      capture_output=True, text=True).stderr


def derive_duration(title, body):
    n = len(title) + len(body)
    return max(MIN_DURATION, min(MAX_DURATION, n / CHARS_PER_SEC + 4.0))


def build(template, overlay_png, out_path, duration, scrim, mute,
          crf=25, maxrate="3000k", bufsize="6000k"):
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    has_audio = (not mute) and probe_has_audio(ffmpeg, template)

    scrim_f = ""
    if scrim > 0:
        lv = round(1.0 - scrim, 3)
        scrim_f = "colorlevels=rimax={0}:gimax={0}:bimax={0},".format(lv)

    filtergraph = (
        "[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        "crop={w}:{h},setsar=1,fps={fps},{scrim}format=rgba[bg];"
        "[bg][1:v]overlay=0:0:format=auto[v]"
    ).format(w=W, h=H, fps=FPS, scrim=scrim_f)

    cmd = [ffmpeg, "-y", "-stream_loop", "-1", "-i", template, "-i", overlay_png]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += [
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", "2:a" if not has_audio else "0:a",
        "-t", str(duration),
        # Phone footage arrives at 25+ Mbps. Instagram re-encodes everything on
        # upload anyway, so capping the bitrate here costs no visible quality
        # and keeps each reel to a size the repo and the API can handle.
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-maxrate", maxrate, "-bufsize", bufsize,
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", "-shortest", out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-3000:])
        raise SystemExit("ffmpeg failed (exit %d)" % proc.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--body", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--scrim", type=float, default=0.35)
    ap.add_argument("--mute", action="store_true")
    ap.add_argument("--preview", action="store_true",
                    help="also write <out>.overlay.png and <out>.frame.png")
    ap.add_argument("--title-top", type=float, default=TITLE_TOP)
    ap.add_argument("--body-top", type=float, default=BODY_TOP)
    ap.add_argument("--body-end", type=float, default=BODY_END)
    ap.add_argument("--margin", type=int, default=MARGIN_X)
    ap.add_argument("--title-size", type=int, default=None)
    ap.add_argument("--body-size", type=int, default=None)
    ap.add_argument("--style", choices=["shadow", "stroke"], default="shadow")
    ap.add_argument("--crf", type=int, default=25,
                    help="quality: lower = better + bigger (18-28 sane)")
    ap.add_argument("--maxrate", default="3000k", help="bitrate ceiling")
    args = ap.parse_args()

    if not os.path.exists(args.template):
        raise SystemExit("Template not found: %s" % args.template)

    duration = args.duration or derive_duration(args.title, args.body)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    opts = {"title_top": args.title_top, "body_top": args.body_top,
            "body_end": args.body_end, "margin": args.margin,
            "title_size": args.title_size, "body_size": args.body_size,
            "style": args.style}

    tmpdir = tempfile.mkdtemp(prefix="city_reels_")
    try:
        overlay_png = os.path.join(tmpdir, "overlay.png")
        info = render_overlay(args.title, args.body, overlay_png, opts)
        if args.preview:
            shutil.copy(overlay_png, args.out + ".overlay.png")
        build(args.template, overlay_png, args.out, duration, args.scrim, args.mute,
              crf=args.crf, maxrate=args.maxrate,
              bufsize="%dk" % (int(args.maxrate.rstrip('k')) * 2))
        if args.preview:
            import imageio_ffmpeg
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-ss",
                            str(min(2.0, duration / 2)), "-i", args.out,
                            "-frames:v", "1", args.out + ".frame.png"],
                           capture_output=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("Wrote %s — %.1fs, %.1f MB, title %spt, body %dpt / %d lines, "
          "body ends at %.0f%% of frame" % (
              args.out, duration, os.path.getsize(args.out) / 1e6,
              info["title_size"] or "-", info["body_size"],
              info["body_lines"], info["body_bottom"] * 100))


if __name__ == "__main__":
    main()
