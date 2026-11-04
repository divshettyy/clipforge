"""ClipForge — YouTube Data API v3 resumable uploader.

Exposes:
    upload(file_path, title, description="", tags=None, privacy="private") -> str
    have_credentials() -> bool

OAuth is InstalledAppFlow. The OAuth client secret must live at
<ROOT>/youtube/client_secret.json (see youtube/SETUP.md for how to obtain it);
the authorized user token is cached at <ROOT>/youtube/token.json and refreshed
automatically when it expires.
"""
import os
import sys

# Make `from config import ROOT` work whether CWD is /home/hakuza (package import
# `from clipforge.youtube.upload import ...`) or ROOT itself (server launched from
# ROOT). Add the project root to sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import ROOT  # noqa: E402

# OAuth scope: upload-only is all we need.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
# "People & Blogs" — a safe, always-valid default category.
CATEGORY_ID = "24"

CLIENT_SECRET_FILE = os.path.join(ROOT, "youtube", "client_secret.json")
TOKEN_FILE = os.path.join(ROOT, "youtube", "token.json")
SETUP_DOC = os.path.join(ROOT, "youtube", "SETUP.md")

_MISSING_SECRET_MSG = (
    "YouTube OAuth client secret not found at:\n"
    "    {path}\n\n"
    "You need to create a Google Cloud OAuth 'Desktop app' client and download "
    "its JSON to that exact path. Step-by-step instructions are in:\n"
    "    {setup}\n"
).format(path=CLIENT_SECRET_FILE, setup=SETUP_DOC)


def have_credentials() -> bool:
    """True if we can plausibly authenticate without a fresh browser consent.

    That means either a cached (and still-usable / refreshable) token exists, or
    at minimum the client_secret.json is present so an interactive flow could run.
    Returns False if client_secret.json is missing (nothing can authenticate).
    """
    if not os.path.exists(CLIENT_SECRET_FILE):
        return False

    if not os.path.exists(TOKEN_FILE):
        # No cached token yet, but a consent flow is possible.
        return True

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds and creds.valid:
            return True
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_token(creds)
                return True
            except Exception:
                # Refresh failed (revoked / network) — still have client secret,
                # so an interactive flow could re-consent.
                return True
        # Token file present but unusable; client secret present.
        return True
    except Exception:
        # Corrupt token file, but client secret is present.
        return True


def _save_token(creds) -> None:
    try:
        with open(TOKEN_FILE, "w") as fh:
            fh.write(creds.to_json())
    except Exception:
        pass


def _get_credentials():
    """Return valid OAuth Credentials, running/refreshing the flow as needed.

    Raises FileNotFoundError (pointing at SETUP.md) if client_secret.json is
    missing and a fresh consent would be required.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None

    # 1. Try the cached token.
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    # 2. Refresh if expired but refreshable.
    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        else:
            creds = None

    # 3. No usable creds — run an interactive InstalledAppFlow.
    if not creds or not creds.valid:
        if not os.path.exists(CLIENT_SECRET_FILE):
            raise FileNotFoundError(_MISSING_SECRET_MSG)

        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
        try:
            # Preferred: spin up a local loopback server and open a browser.
            creds = flow.run_local_server(port=0)
        except Exception:
            # Headless / no-browser fallback: paste-the-code console flow.
            try:
                creds = flow.run_console()
            except AttributeError:
                # Very new google-auth-oauthlib dropped run_console(); emulate it.
                creds = _manual_console_flow(flow)

    if not creds or not creds.valid:
        raise RuntimeError("Failed to obtain valid YouTube OAuth credentials.")

    _save_token(creds)
    return creds


def _manual_console_flow(flow):
    """Minimal out-of-band console flow for google-auth-oauthlib builds that no
    longer ship run_console(). Prints an auth URL, reads the pasted code."""
    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    auth_url, _ = flow.authorization_url(prompt="consent")
    print("Open this URL in a browser, authorize, and paste the code here:")
    print(auth_url)
    code = input("Enter the authorization code: ").strip()
    flow.fetch_token(code=code)
    return flow.credentials


def _build_service(creds):
    from googleapiclient.discovery import build

    # cache_discovery=False avoids noisy warnings / file-cache writes.
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(file_path: str, title: str, description: str = "",
           tags=None, privacy: str = "private") -> str:
    """Upload a video to YouTube via the Data API v3 resumable protocol.

    Args:
        file_path: absolute path to the video file to upload.
        title: video title.
        description: video description.
        tags: optional list of tag strings.
        privacy: "private" (default), "unlisted", or "public".

    Returns:
        The YouTube video id (str).
    """
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError("Video file to upload not found: {}".format(file_path))

    privacy = (privacy or "private").lower()
    if privacy not in ("private", "unlisted", "public"):
        privacy = "private"

    if tags is None:
        tags = []

    creds = _get_credentials()
    youtube = _build_service(creds)

    body = {
        "snippet": {
            "title": title or "Untitled",
            "description": description or "",
            "tags": list(tags),
            "categoryId": CATEGORY_ID,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            # (status.progress() available here if progress reporting is wanted)
    except HttpError as e:
        raise RuntimeError("YouTube upload failed: {}".format(e)) from e

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError("YouTube upload returned no video id: {}".format(response))
    return video_id


if __name__ == "__main__":
    # Simple manual test: python youtube/upload.py <file> <title>
    import argparse

    ap = argparse.ArgumentParser(description="Upload a video to YouTube.")
    ap.add_argument("file")
    ap.add_argument("title")
    ap.add_argument("--description", default="")
    ap.add_argument("--privacy", default="private",
                    choices=["private", "unlisted", "public"])
    ap.add_argument("--tags", default="", help="comma-separated")
    args = ap.parse_args()

    tag_list = [t.strip() for t in args.tags.split(",") if t.strip()]
    vid = upload(args.file, args.title, args.description, tag_list, args.privacy)
    print("Uploaded. video_id =", vid)
    print("https://youtu.be/{}".format(vid))
