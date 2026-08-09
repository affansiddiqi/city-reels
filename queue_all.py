#!/usr/bin/env python3
"""Queue every rendered reel, one per hour, starting at the next whole hour."""
import datetime, json, os, sys
from zoneinfo import ZoneInfo

ORDER = ["baku_teens","baku_it_guys","baku_it_girls","baku_cars","baku_founders",
         "baku_marketing","baku_students","baku_med","baku_foreign_students",
         "baku_gym","baku_freelancers","baku_wedding","baku_marriage",
         "baku_cabin_crew","baku_abroad"]

HERE = os.path.dirname(os.path.abspath(__file__))
tz = ZoneInfo(json.load(open(os.path.join(HERE,"config.json")))["timezone"])
queue = json.load(open(os.path.join(HERE,"queue.json")))
done = {i["id"] for i in queue if i.get("result") == "posted"}
known = {i["id"] for i in queue}

# First reel goes out immediately: give it a timestamp already in the past so
# the very next workflow run treats it as due. Everything after it lands on the
# whole hour -- 23:00, 00:00, 01:00 and so on.
now = datetime.datetime.now(tz)
first = now - datetime.timedelta(minutes=5)
slot = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
is_first = True

added, missing = 0, []
for stem in ORDER:
    path = os.path.join(HERE, "content", stem + ".json")
    for t in json.load(open(path)):
        if t["id"] in done or t["id"] in known:
            continue
        vid = os.path.join(HERE, "videos", t["id"] + ".mp4")
        if not os.path.exists(vid):
            missing.append(t["id"]); continue
        when = first if is_first else slot
        queue.append({"id": t["id"], "caption": t["caption"],
                      "video": "videos/%s.mp4" % t["id"],
                      "scheduled_at": when.strftime("%Y-%m-%d %H:%M"),
                      "status": "approved", "result": None, "post_id": None,
                      "posted_at_utc": None, "error": None})
        if is_first:
            is_first = False
        else:
            slot += datetime.timedelta(hours=1)
        added += 1

json.dump(queue, open(os.path.join(HERE,"queue.json"),"w"), indent=2, ensure_ascii=False)
live = [i for i in queue if i.get("result") != "posted"]
print("queued %d reels, one per hour" % added)
if missing:
    print("SKIPPED (no video yet): %d -> %s" % (len(missing), ", ".join(missing[:6])))
if live:
    print("first: %s   last: %s" % (live[0]["scheduled_at"], live[-1]["scheduled_at"]))
    span = (datetime.datetime.strptime(live[-1]["scheduled_at"], "%Y-%m-%d %H:%M")
            - datetime.datetime.strptime(live[0]["scheduled_at"], "%Y-%m-%d %H:%M"))
    print("covers %.1f days" % (span.total_seconds()/86400))
