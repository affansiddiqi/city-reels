# city-reels

Posts text-over-video Reels to **one Instagram account**, automatically, on a
schedule — including while your laptop is closed.

This project is completely independent. It has its own code, its own schedule,
its own Instagram account and its own credentials. It shares nothing with any
other project.

---

## The idea in one paragraph

You write the words. Your laptop turns each one into a video and adds it to a
schedule. You push that to GitHub. From then on GitHub's servers — not your
laptop — post them one at a time at the times you chose. Your laptop only has
to be on when you're making videos, never when they're being posted.

```
   YOUR LAPTOP                          GITHUB'S SERVERS (always on)
   ───────────                          ────────────────────────────
   content/*.json   ─┐
   templates/*.mp4  ─┼─► build.py ──►  videos/*.mp4  ──► git push ──►  post.py
   (your words)      │                 queue.json                      runs every
                     │                 (the schedule)                  hour and
                     ┘                                                 posts what's due
```

---

## The files

| File | What it is |
|---|---|
| `content/*.json` | **Your words.** One file per series. Each entry has a title, a body and a caption. |
| `templates/` | **Your background videos.** Drop your clip in here. |
| `render.py` | Burns one text block onto the template and writes one MP4. |
| `build.py` | Runs `render.py` over a whole content file and adds everything to the schedule. **This is the one you actually run.** |
| `queue.json` | **The schedule.** Every video, when it posts, and whether it's approved. |
| `videos/` | The finished MP4s. Instagram downloads them from here. |
| `post.py` | Runs on GitHub's servers every hour. Posts whatever is due. You never run this yourself. |
| `auth.py` | One-time setup. Connects your Instagram account. |
| `config.json` | Settings: timezone, how many posts per run. |
| `.github/workflows/post.yml` | The hourly timer. |

---

## Nothing posts by accident

Every new item enters the queue as `"status": "draft"`. The publisher ignores
drafts completely. A video only ever goes live after **you** change that one
word to `"approved"` and push.

`MAX_PER_RUN` (default `1`) caps how many can post per hourly run. If you ever
approve 40 items at once, they go out one an hour, not all at once — which is
what would otherwise get a new account flagged as spam.

---

## First-time setup

**1. Switch to the right GitHub account** (you have several logged in):

```bash
gh auth switch --user affansiddiqi
```

**2. Make the Instagram account a Business account** and link it to a Facebook
Page. In the Instagram app: Settings → Account type and tools → Switch to
professional account → Business. The posting API does not work with Personal or
Creator accounts.

**3. Create a Meta app** at developers.facebook.com, then:

```bash
cp secrets.example.json secrets.local.json
```

Paste your App ID and App Secret into `secrets.local.json`. Add
`http://localhost:8000/callback` to the app's Valid OAuth Redirect URIs
(Facebook Login → Settings). `secrets.local.json` is gitignored.

**4. Connect the account:**

```bash
python3 auth.py
```

It opens a browser, you click Allow, it writes `tokens.json` and prints the two
commands for the next step.

**5. Create the GitHub repo and push:**

```bash
gh repo create city-reels --public --source=. --push
```

It must be **public**, because Instagram downloads the videos from the repo over
a plain URL.

**6. Set the two secrets** (the commands `auth.py` printed):

```bash
gh secret set IG_PAGE_ACCESS_TOKEN --body "$(python3 -c "import json;print(json.load(open('tokens.json'))['page_access_token'])")"
```

```bash
gh secret set IG_BUSINESS_ACCOUNT_ID --body "$(python3 -c "import json;print(json.load(open('tokens.json'))['ig_business_account_id'])")"
```

---

## Making and posting a batch

```bash
python3 build.py --content content/baku_teens.json --template templates/your-clip.mp4 --start "2026-08-12 09:00" --every-hours 4
```

`--every-hours 4` gives six posts a day. Use `1` for hourly, `24` for once a
day. Then watch the videos in `videos/`, change `"draft"` to `"approved"` in
`queue.json` for the ones you want, and:

```bash
git add -A && git commit -m "add baku batch" && git push
```

That's it. They post themselves from there.

---

## Adjusting how the text looks

`build.py` passes these through to `render.py`; you can also call `render.py`
directly on a single item with `--preview` to get a still frame to check.

| Flag | Does |
|---|---|
| `--body-top 0.30` | Move the body block up or down (fraction of frame height) |
| `--title-top 0.135` | Move the title |
| `--body-size 34` | Force a text size instead of auto-fitting |
| `--scrim 0.5` | Darken the footage more so white text reads |
| `--style stroke` | Hard black outline instead of the soft shadow |
| `--duration 30` | Fixed length instead of one derived from reading time |

---

## Things worth knowing

**Trending audio is not possible through the API.** Instagram does not allow it
for posts published this way — the video keeps whatever audio is baked into the
file. If a particular reel really needs a trending sound, post that one by hand
from the app.

**GitHub's hourly timer is approximate.** Runs are often 5–20 minutes late and
occasionally skipped when GitHub is busy. Nothing is lost — a missed item just
posts on the next run.

**Videos live in this repo, and git never forgets.** At a few posts a day that's
fine for a long time. If you push toward 20+ a day, the repo will grow by
several GB a month and you'll want to move `videos/` to Cloudflare R2 or S3 and
point `public_assets_base` in `config.json` at that instead. Only that one line
has to change.

**Instagram's hard ceiling is 50 published posts per rolling 24 hours.**

**The token expires after about 60 days.** When it does, the hourly run fails
and GitHub emails you. Re-run `python3 auth.py` and set the two secrets again.
