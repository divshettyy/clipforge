"""One-time YouTube OAuth for ClipForge, WSL-friendly.

Google retired the OOB/console flow, and WSL has no default browser, so we run a
loopback server on a FIXED port and DON'T auto-open a browser — we print the URL
for you to open in your Windows browser. The redirect comes back to
http://localhost:8900/ which WSL2 forwards from Windows. Writes youtube/token.json.

Run:  /home/hakuza/venv/bin/python youtube/auth_youtube.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET = os.path.join(ROOT, "youtube", "client_secret.json")
TOKEN = os.path.join(ROOT, "youtube", "token.json")
PORT = 8900


def main():
    if not os.path.exists(CLIENT_SECRET):
        sys.exit("client_secret.json missing at %s — see youtube/SETUP.md" % CLIENT_SECRET)
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(
        host="0.0.0.0",
        port=PORT,
        open_browser=False,
        authorization_prompt_message=(
            "\n>>> Open this URL in your WINDOWS browser, sign in as the test-user "
            "account, approve:\n\n{url}\n\n"
            ">>> (If you see 'unverified app': Advanced -> Go to ClipForge (unsafe).)\n"
        ),
        success_message="Done — you can close this tab and return to the terminal.",
    )
    with open(TOKEN, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN, 0o600)
    print("token.json written to", TOKEN)


if __name__ == "__main__":
    main()
