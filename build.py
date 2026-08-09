#!/usr/bin/env python3
"""
Batch-renders a content file into videos and adds them to the queue.

This runs on YOUR LAPTOP, not in the cloud. You run it once per batch; the
cloud then posts those videos out on schedule whether your laptop is on or not.

New items are added with "status": "draft" on purpose. Nothing can go live
until you change that to "approved" yourself.

A content file is a JSON list:
    [
      {"id": "baku-01",
       "title": "How to (Accidentally) Meet the Rich Teens of Baku. Tip #1",
       "body": "Get into TEAS, Baku Oxford...",
       "caption": "Tip #1 of a series 😭 #baku #azerbaijan"},
      ...
    ]

Usage:
    python3 build.py --content content/baku_teens.json \
        --template templates/baku-night.mp4 \
        --start "2026-08-12 09:00" --every-hours 4
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(HERE, "queue.json")
RENDER = os.path.join(HERE, "render.py")
VIDEOS_DIR = os.path.join(HERE, "videos")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True, help="path to a content JSON file")
    ap.add_argument("--template", required=True, help="template video to burn onto")
    ap.add_argument("--start", help="first slot: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--time", default="09:00", help="used when --start has no time")
    ap.add_argument("--every-hours", type=float, default=24.0,
                    help="hours between posts (1 = hourly, 24 = once a day)")
    ap.add_argument("--scrim", type=float, default=0.35,
                    help="0-1 darkening of the footage so text reads")
    ap.add_argument("--duration", type=float, default=None,
                    help="seconds per reel (default: render.py's 7s)")
    ap.add_argument("--mute", action="store_true")
    ap.add_argument("--no-queue", action="store_true",
                    help="render only, don't touch queue.json")
    ap.add_argument("--force", action="store_true", help="re-render existing videos")
    args = ap.parse_args()

    if not os.path.exists(args.template):
        raise SystemExit("Template not found: %s" % args.template)

    queueing = not args.no_queue
    if queueing and not args.start:
        raise SystemExit("--start is required unless you pass --no-queue")

    base_dt = None
    if args.start:
        s = args.start.strip()
        base_dt = (datetime.datetime.strptime(s, "%Y-%m-%d %H:%M") if " " in s
                   else datetime.datetime.strptime("%s %s" % (s, args.time),
                                                   "%Y-%m-%d %H:%M"))

    items = json.load(open(args.content))
    os.makedirs(VIDEOS_DIR, exist_ok=True)

    rendered = skipped = 0
    new_items = []

    for i, tip in enumerate(items):
        name = "%s.mp4" % tip["id"]
        out_path = os.path.join(VIDEOS_DIR, name)

        if os.path.exists(out_path) and not args.force:
            print("exists, skipping render: %s" % name)
            skipped += 1
        else:
            cmd = [sys.executable, RENDER,
                   "--template", args.template,
                   "--title", tip.get("title", ""),
                   "--body", tip["body"],
                   "--out", out_path,
                   "--scrim", str(args.scrim)]
            if args.duration:
                cmd += ["--duration", str(args.duration)]
            if args.mute:
                cmd += ["--mute"]
            print("rendering %s ..." % name)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if proc.returncode != 0:
                print("::error:: %s failed:\n%s" % (tip["id"], proc.stderr[-800:]))
                continue
            print("  " + proc.stdout.strip())
            rendered += 1

        if queueing:
            when = base_dt + datetime.timedelta(hours=i * args.every_hours)
            new_items.append({
                "id": tip["id"],
                "caption": tip.get("caption") or tip.get("title", ""),
                "video": "videos/" + name,
                "scheduled_at": when.strftime("%Y-%m-%d %H:%M"),
                "status": "draft",
                "result": None,
                "post_id": None,
                "posted_at_utc": None,
                "error": None,
            })

    if queueing and new_items:
        queue = json.load(open(QUEUE_PATH)) if os.path.exists(QUEUE_PATH) else []
        known = {it.get("id") for it in queue}
        fresh = [it for it in new_items if it["id"] not in known]
        queue.extend(fresh)
        json.dump(queue, open(QUEUE_PATH, "w"), indent=2, ensure_ascii=False)
        print("\nQueued %d new draft item(s); %d were already in the queue."
              % (len(fresh), len(new_items) - len(fresh)))
        if fresh:
            print("First slot: %s   Last slot: %s"
                  % (fresh[0]["scheduled_at"], fresh[-1]["scheduled_at"]))

    print("Rendered %d, skipped %d." % (rendered, skipped))
    print("\nNext steps:")
    print("  1. Watch the videos in videos/ and make sure you like them.")
    print("  2. Change \"status\" to \"approved\" in queue.json for the ones to post.")
    print("  3. git add -A && git commit -m 'add batch' && git push")


if __name__ == "__main__":
    main()
