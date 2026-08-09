#!/usr/bin/env python3
"""
One-time setup: connects your Instagram account and saves its credentials.

Run this on your laptop. It opens a Facebook consent screen, you click Allow,
and it writes tokens.json with the two values the cloud publisher needs.

Before running you need:
  1. The Instagram account switched to a BUSINESS account (not Creator, not
     Personal) and linked to a Facebook Page. Do that in the Instagram app:
     Settings -> Account type and tools -> Switch to professional account.
  2. A Meta app at developers.facebook.com, with your App ID and App Secret
     copied into secrets.local.json (see secrets.example.json).
  3. "http://localhost:8000/callback" added to that app's Valid OAuth Redirect
     URIs, under Facebook Login -> Settings.

Pure standard library -- no pip installs needed.
"""
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
SECRETS_PATH = os.path.join(HERE, "secrets.local.json")
TOKENS_PATH = os.path.join(HERE, "tokens.json")

if not os.path.exists(SECRETS_PATH):
    raise SystemExit(
        "Missing secrets.local.json.\n"
        "Copy secrets.example.json to secrets.local.json and paste in your\n"
        "Meta App ID and App Secret from developers.facebook.com."
    )
CONFIG.update(json.load(open(SECRETS_PATH)))   # app_id, app_secret

GRAPH = "https://graph.facebook.com/v21.0"
AUTH_URL = "https://www.facebook.com/v21.0/dialog/oauth"
STATE = "city_reels_auth"
_result = {"code": None, "error": None}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        if "error" in params:
            _result["error"] = params.get("error_description", params["error"])[0]
            body = "<h2>Authorisation failed</h2><p>%s</p>" % _result["error"]
        else:
            _result["code"] = params.get("code", [None])[0]
            body = "<h2>Connected.</h2><p>You can close this tab.</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def get_json(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as resp:
        return json.load(resp)


def main():
    consent_url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": CONFIG["app_id"],
        "redirect_uri": CONFIG["redirect_uri"],
        "state": STATE,
        "scope": CONFIG["scopes"],
        "response_type": "code",
    })
    print("Opening your browser. If it doesn't open, paste this in:\n\n%s\n"
          % consent_url)

    server = http.server.HTTPServer(("127.0.0.1", 8000), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        webbrowser.open(consent_url)
    except Exception:
        pass

    print("Waiting for you to click Allow (90 second timeout)...")
    deadline = time.time() + 90
    while _result["code"] is None and _result["error"] is None and time.time() < deadline:
        time.sleep(0.5)
    server.shutdown()

    if _result["error"]:
        raise SystemExit("Authorisation failed: %s" % _result["error"])
    if not _result["code"]:
        raise SystemExit("Timed out waiting for authorisation.")

    short = get_json(GRAPH + "/oauth/access_token", {
        "client_id": CONFIG["app_id"], "client_secret": CONFIG["app_secret"],
        "redirect_uri": CONFIG["redirect_uri"], "code": _result["code"]})

    long_ = get_json(GRAPH + "/oauth/access_token", {
        "grant_type": "fb_exchange_token", "client_id": CONFIG["app_id"],
        "client_secret": CONFIG["app_secret"],
        "fb_exchange_token": short["access_token"]})
    user_token = long_["access_token"]
    expires_in = long_.get("expires_in", 60 * 24 * 3600)

    pages = get_json(GRAPH + "/me/accounts", {"access_token": user_token})
    if not pages.get("data"):
        raise SystemExit(
            "No Facebook Pages found. The account you logged in as must be an\n"
            "admin of the Page your Instagram account is linked to.")

    candidates = []
    for page in pages["data"]:
        info = get_json(GRAPH + "/%s" % page["id"], {
            "fields": "instagram_business_account{id,username},name",
            "access_token": page["access_token"]})
        if info.get("instagram_business_account"):
            candidates.append({
                "page_id": page["id"],
                "page_name": info.get("name"),
                "page_access_token": page["access_token"],
                "ig_business_account_id": info["instagram_business_account"]["id"],
                "ig_username": info["instagram_business_account"].get("username", "?"),
            })

    if not candidates:
        names = ", ".join(p.get("name", "?") for p in pages["data"])
        raise SystemExit(
            "None of your Pages [%s] has a linked Instagram Business account.\n"
            "Link it in the Instagram app first, then re-run this." % names)

    print("\nInstagram accounts this login can post to:\n")
    for c in candidates:
        print("  @%-24s  (Page: %s)" % (c["ig_username"], c["page_name"]))
    print()

    want = None
    for i, a in enumerate(sys.argv):
        if a == "--account" and i + 1 < len(sys.argv):
            want = sys.argv[i + 1].lstrip("@").lower()

    if want:
        match = [c for c in candidates if c["ig_username"].lower() == want]
        if not match:
            raise SystemExit("No account called @%s in the list above. "
                             "Nothing was saved." % want)
        chosen = match[0]
    elif len(candidates) == 1:
        chosen = candidates[0]
    else:
        raise SystemExit(
            "More than one account is available, so nothing was saved.\n"
            "Re-run naming the one you want, e.g.:\n"
            "    python3 auth.py --account mee.tpeople")

    chosen["expires_at"] = int(time.time()) + int(expires_in)
    json.dump(chosen, open(TOKENS_PATH, "w"), indent=2)

    print("\nConnected @%s (Page: %s)" % (chosen["ig_username"], chosen["page_name"]))
    print("Saved to tokens.json — this file is gitignored, keep it private.\n")
    print("Now set the two GitHub secrets:\n")
    print('  gh secret set IG_PAGE_ACCESS_TOKEN --body "$(python3 -c '
          "\"import json;print(json.load(open('tokens.json'))['page_access_token'])\")\"")
    print('  gh secret set IG_BUSINESS_ACCOUNT_ID --body "$(python3 -c '
          "\"import json;print(json.load(open('tokens.json'))['ig_business_account_id'])\")\"")


if __name__ == "__main__":
    main()
