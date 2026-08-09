#!/usr/bin/env python3
"""
Alternative to auth.py that skips the OAuth redirect entirely.

Use this when Facebook refuses the login redirect (the "isn't using a secure
connection" error). Instead of running the OAuth dance locally, you generate a
User Token by hand in Meta's Graph API Explorer and hand it to this script,
which does the rest: upgrades it to a long-lived token, finds your Pages, finds
the Instagram account linked to each, and writes tokens.json.

How to get the token:
  1. Open  https://developers.facebook.com/tools/explorer
  2. Top right, "Meta App": pick the same app this project uses.
  3. "User or Page": choose  User Token.
  4. Add these permissions in the box below:
         pages_show_list
         pages_read_engagement
         instagram_basic
         instagram_content_publish
  5. Click "Generate Access Token" and approve. When it asks which Pages to
     allow, make sure the NEW page (Meet People) is ticked.
  6. Copy the token string it shows.

Then save it to a file and run this -- using a file rather than a command-line
argument keeps the token out of your shell history:

    pbpaste > ~/fbtoken.txt          # or just paste it into that file
    python3 connect.py --token-file ~/fbtoken.txt --account mee.tpeople
    rm ~/fbtoken.txt

The token you paste is short-lived (about an hour). That's fine -- this script
immediately exchanges it for a long-lived one (about 60 days), which is what
actually gets stored and used.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
SECRETS_PATH = os.path.join(HERE, "secrets.local.json")
TOKENS_PATH = os.path.join(HERE, "tokens.json")

if not os.path.exists(SECRETS_PATH):
    raise SystemExit("Missing secrets.local.json — copy secrets.example.json "
                     "and fill in your Meta App ID and Secret.")
CONFIG.update(json.load(open(SECRETS_PATH)))

GRAPH = "https://graph.facebook.com/v21.0"


def get(path, **params):
    url = GRAPH + path + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            msg = json.loads(detail)["error"]["message"]
        except Exception:
            msg = detail[:300]
        raise SystemExit("Facebook rejected the request: %s" % msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", help="file containing the pasted User Token")
    ap.add_argument("--token", help="the token itself (ends up in shell history)")
    ap.add_argument("--account", help="which Instagram username to save")
    ap.add_argument("--list", action="store_true",
                    help="just show what's available, save nothing")
    args = ap.parse_args()

    if args.token_file:
        token = open(os.path.expanduser(args.token_file)).read().strip()
    elif args.token:
        token = args.token.strip()
    else:
        raise SystemExit("Pass --token-file (preferred) or --token.")
    if not token:
        raise SystemExit("That token file is empty.")

    who = get("/me", fields="id,name", access_token=token)
    print("Logged in as: %s" % who.get("name"))

    # Short-lived -> long-lived (~60 days). Harmless if it's already long-lived.
    try:
        long_ = get("/oauth/access_token", grant_type="fb_exchange_token",
                    client_id=CONFIG["app_id"], client_secret=CONFIG["app_secret"],
                    fb_exchange_token=token)
        token = long_["access_token"]
        expires_in = long_.get("expires_in", 60 * 24 * 3600)
        print("Upgraded to a long-lived token (~%d days)." % (expires_in // 86400))
    except SystemExit:
        expires_in = 3600
        print("Could not upgrade the token; continuing with the one you gave.")

    pages = get("/me/accounts", fields="name,access_token", access_token=token)
    if not pages.get("data"):
        raise SystemExit(
            "No Pages came back. Either this login doesn't administer any Page, "
            "or you didn't tick the Page when granting permissions.")

    print("\nPages this login can manage: %d" % len(pages["data"]))
    candidates = []
    for page in pages["data"]:
        info = get("/%s" % page["id"],
                   fields="instagram_business_account{id,username},name",
                   access_token=page["access_token"])
        ig = info.get("instagram_business_account")
        print("  %-30s -> %s" % (
            info.get("name"),
            ("@" + ig["username"]) if ig and ig.get("username")
            else ("linked (id %s)" % ig["id"]) if ig
            else "no Instagram linked"))
        if ig:
            candidates.append({
                "page_id": page["id"],
                "page_name": info.get("name"),
                "page_access_token": page["access_token"],
                "ig_business_account_id": ig["id"],
                "ig_username": ig.get("username", "?"),
            })

    if args.list:
        return
    if not candidates:
        raise SystemExit(
            "\nNone of those Pages has an Instagram Business account linked.\n"
            "In the Instagram app: Settings -> Account type and tools -> switch to\n"
            "a Business account, then link it to the Page. Then re-run this.")

    if args.account:
        want = args.account.lstrip("@").lower()
        match = [c for c in candidates if c["ig_username"].lower() == want]
        if not match:
            raise SystemExit("\nNo account called @%s above. Nothing saved." % want)
        chosen = match[0]
    elif len(candidates) == 1:
        chosen = candidates[0]
    else:
        raise SystemExit("\nSeveral accounts available — re-run with "
                         "--account <username> to pick one. Nothing saved.")

    chosen["expires_at"] = int(time.time()) + int(expires_in)
    json.dump(chosen, open(TOKENS_PATH, "w"), indent=2)
    os.chmod(TOKENS_PATH, 0o600)

    print("\nSaved @%s (Page: %s) to tokens.json"
          % (chosen["ig_username"], chosen["page_name"]))
    print("Delete the file you pasted the token into — it is no longer needed.")


if __name__ == "__main__":
    main()
