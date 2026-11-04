# YouTube Upload — OAuth Setup

ClipForge uploads clips with the **YouTube Data API v3** using an OAuth
"Desktop app" client. You need to create that client once in the Google Cloud
Console, download its JSON, and save it as:

```
/home/hakuza/clipforge/youtube/client_secret.json
```

After that, the **first** upload opens a browser once so you can grant consent.
The resulting token is cached at `youtube/token.json` and refreshed
automatically from then on — no further browser prompts.

---

## Exact click-path

### 1. Create a Google Cloud project
1. Go to <https://console.cloud.google.com/>.
2. Top bar → project dropdown → **New Project**.
3. Name it e.g. `ClipForge`, click **Create**, then select it.

### 2. Enable the YouTube Data API v3
1. Left menu → **APIs & Services → Library**
   (or go to <https://console.cloud.google.com/apis/library>).
2. Search for **"YouTube Data API v3"**.
3. Click it, then click **Enable**.

### 3. Configure the OAuth consent screen
1. Left menu → **APIs & Services → OAuth consent screen**.
2. **User Type:** choose **External**, click **Create**.
3. **App information:** App name = `ClipForge`, User support email = your own
   Google account, Developer contact email = your own Google account. Click
   **Save and Continue**.
4. **Scopes:** click **Add or Remove Scopes**, filter for
   `youtube.upload`, tick
   **`https://www.googleapis.com/auth/youtube.upload`**, click **Update**, then
   **Save and Continue**.
5. **Test users:** click **Add Users** and add **your own Google account**
   (the account that owns the YouTube channel you'll upload to). Click
   **Save and Continue**.
   - The app stays in "Testing" mode — that is fine. Only the test users you
     add here can authorize it, which is exactly what we want.
6. Review and go **Back to Dashboard**.

### 4. Create the OAuth client ID (Desktop app)
1. Left menu → **APIs & Services → Credentials**.
2. **+ Create Credentials → OAuth client ID**.
3. **Application type:** select **Desktop app**.
4. Name it e.g. `ClipForge Desktop`, click **Create**.
5. In the dialog, click **Download JSON**.

### 5. Save the client secret to the right path
Move/rename the downloaded file to exactly:

```
/home/hakuza/clipforge/youtube/client_secret.json
```

For example, if it landed in your Windows Downloads folder:

```bash
mv ~/Downloads/client_secret_*.json /home/hakuza/clipforge/youtube/client_secret.json
# or from WSL, if downloaded on the Windows side:
# cp /mnt/c/Users/<you>/Downloads/client_secret_*.json \
#    /home/hakuza/clipforge/youtube/client_secret.json
```

---

## First run (one-time browser consent)

The first time `youtube.upload.upload(...)` runs (e.g. clicking
**Upload to YouTube** in the UI, or the manual test below), it will:

1. Open a browser window to Google's consent screen
   (via `run_local_server`; if no browser is available it falls back to a
   console "paste this code" flow).
2. Ask you to sign in with the **test-user** account you added in step 3.5 and
   approve the `youtube.upload` scope.
3. Write the authorized token to `youtube/token.json`.

You will **not** be prompted again — the cached token is refreshed
automatically when it expires.

Manual test from the project root:

```bash
cd /home/hakuza/clipforge
/home/hakuza/venv/bin/python youtube/upload.py \
    data/jobs/demo-igl-ep8/clips/01_*.mp4 "ClipForge test" --privacy private
```

> You may see an "unverified app" warning because the app is in Testing mode —
> click **Advanced → Go to ClipForge (unsafe)** to proceed. This is expected
> for a personal/test OAuth client and is safe for your own account.

---

## Quota — plan your uploads

The YouTube Data API has a **default quota of 10,000 units/day**, and a single
video insert (upload) costs **~1,600 units**. That works out to roughly:

```
10,000 / 1,600  ≈  6 uploads per day
```

So with default quota you can upload about **6 clips per day**. Reads
(listing/checking a video) are cheap by comparison, but uploads dominate.
If you need more, request a quota increase on the Google Cloud project — but
for ClipForge's clip-at-a-time workflow, 6/day is usually enough.

---

## Files this creates

| File | What it is | Commit? |
|------|------------|---------|
| `youtube/client_secret.json` | Your OAuth client secret (you download it) | **No — keep secret** |
| `youtube/token.json` | Cached authorized user token (auto-created) | **No — keep secret** |

Both files are secrets. Do not commit them or share them.
