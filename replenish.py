#!/usr/bin/env python3
"""
Keeps the queue topped up automatically. Runs in GitHub Actions before post.py.

Any item in content/*.json that is not yet in queue.json gets rendered and
scheduled, one per hour, starting after whatever is already queued. Items are
added as "approved" -- this is the unattended path, so nothing waits on a human.

Rendering happens on the runner, which is why render.py resolves fonts on both
macOS and Linux. The template video is committed to the repo, so the runner has
everything it needs.

Two guards worth knowing about:
  * A render that produces a truncated file is discarded and retried rather than
    being queued -- a 48-byte MP4 once jammed the queue for nine days.
  * MAX_NEW_PER_RUN caps how much work a single run does, so a big new content
    drop is rendered over several runs instead of timing the job out.
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
QUEUE_PATH = os.path.join(HERE, "queue.json")
CONTENT_DIR = os.path.join(HERE, "content")
VIDEOS_DIR = os.path.join(HERE, "videos")
RENDER = os.path.join(HERE, "render.py")

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(CONFIG.get("timezone", "Asia/Baku"))
except Exception:
    TZ = None

EVERY_HOURS = float(os.environ.get("EVERY_HOURS", CONFIG.get("every_hours", 1)))
MAX_NEW_PER_RUN = int(os.environ.get("MAX_NEW_PER_RUN",
                                     CONFIG.get("max_new_per_run", 6)))
SCRIM = str(CONFIG.get("scrim", 0.2))
TEMPLATE = os.path.join(HERE, CONFIG.get("template", "templates/Template.MP4"))
MIN_VALID_BYTES = 100_000


def now_local():
    return datetime.datetime.now(TZ).replace(tzinfo=None) if TZ else datetime.datetime.now()


def is_valid_video(path):
    if not os.path.exists(path) or os.path.getsize(path) < MIN_VALID_BYTES:
        return False
    try:
        import imageio_ffmpeg
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error",
                            "-i", path, "-f", "null", "-"],
                           capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


def content_files():
    return sorted(f for f in os.listdir(CONTENT_DIR) if f.endswith(".json"))


def render(tip, out_path):
    cmd = [sys.executable, RENDER, "--template", TEMPLATE,
           "--title", tip.get("title", ""), "--body", tip["body"],
           "--out", out_path, "--scrim", SCRIM]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        print("::warning::render failed for %s: %s" % (tip["id"], r.stderr[-400:]))
        return False
    if not is_valid_video(out_path):
        print("::warning::render produced an invalid file for %s; discarding"
              % tip["id"])
        try:
            os.remove(out_path)
        except OSError:
            pass
        return False
    return True


def main():
    queue = json.load(open(QUEUE_PATH)) if os.path.exists(QUEUE_PATH) else []
    known = {i.get("id") for i in queue}
    os.makedirs(VIDEOS_DIR, exist_ok=True)

    pending = [i for i in queue if i.get("result") != "posted"]
    print("queue: %d total, %d not yet posted" % (len(queue), len(pending)))

    # Continue from the last scheduled slot, or from the next whole hour if the
    # queue has fully drained.
    slots = [i["scheduled_at"] for i in queue if i.get("scheduled_at")]
    nxt = now_local().replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    if slots:
        try:
            last = datetime.datetime.strptime(max(slots), "%Y-%m-%d %H:%M")
            nxt = max(nxt, last + datetime.timedelta(hours=EVERY_HOURS))
        except ValueError:
            pass

    added = 0
    for fname in content_files():
        if added >= MAX_NEW_PER_RUN:
            break
        for tip in json.load(open(os.path.join(CONTENT_DIR, fname))):
            if added >= MAX_NEW_PER_RUN:
                break
            if tip["id"] in known:
                continue
            out = os.path.join(VIDEOS_DIR, tip["id"] + ".mp4")
            if not is_valid_video(out):
                print("rendering %s ..." % tip["id"])
                if not render(tip, out):
                    continue
            queue.append({
                "id": tip["id"],
                "caption": tip.get("caption") or tip.get("title", ""),
                "video": "videos/%s.mp4" % tip["id"],
                "scheduled_at": nxt.strftime("%Y-%m-%d %H:%M"),
                "status": "approved",
                "result": None, "post_id": None,
                "posted_at_utc": None, "error": None,
            })
            known.add(tip["id"])
            nxt += datetime.timedelta(hours=EVERY_HOURS)
            added += 1
            print("  queued for %s" % queue[-1]["scheduled_at"])

    if added:
        json.dump(queue, open(QUEUE_PATH, "w"), indent=2, ensure_ascii=False)

    remaining = sum(1 for f in content_files()
                    for t in json.load(open(os.path.join(CONTENT_DIR, f)))
                    if t["id"] not in known)
    still = sum(1 for i in queue if i.get("result") != "posted")
    print("Added %d. %d scripts still unqueued. %d reels waiting to post."
          % (added, remaining, still))
    if still == 0 and remaining == 0:
        print("::warning::Content bank is empty -- nothing left to post. "
              "Add a new file to content/ to keep the account running.")


if __name__ == "__main__":
    main()
