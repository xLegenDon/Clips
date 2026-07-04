# Clips — Daily Instagram Reel Poster

Automatically posts up to 10 clips a day from `clips/pending/` to Instagram as Reels,
using the **Instagram API with Instagram Login** (Content Publishing). GitHub
Actions runs the posting script 10 times a day, spreading the day's clips out
instead of posting them all in one burst.

## How it works

1. Drop video files (`.mp4` or `.mov`) into `clips/pending/`.
2. Optionally add a caption for a clip by creating a same-named `.txt` file
   next to it, e.g. `clip_01.mp4` + `clip_01.txt`. The contents of
   `hashtags.txt` (repo root) are automatically appended to every caption —
   edit that file to change your default hashtags.
3. Every 2 hours, GitHub Actions runs `scripts/post_daily_clips.py`, which:
   - picks up to `DAILY_POST_LIMIT` (1 per scheduled run, so 10/day total)
     pending clips, oldest filename first
   - uploads each clip to Cloudinary to get a public video URL
   - creates an Instagram Reels media container via the Graph API
   - waits for Instagram to finish processing the video
   - publishes it
   - logs the result to `clips/posted_log.csv` (used for insights tracking)
   - moves the posted clip (and caption) into `clips/posted/<date>/`

Instagram's API requires each video to be reachable at a public HTTPS
URL — it cannot accept a direct file upload. Cloudinary's free tier is used
to get that public URL without needing to make this repo public.

Two more workflows run on their own schedule:
- **Refresh Instagram Token** (weekly) — refreshes `IG_ACCESS_TOKEN` and
  writes the new value back into the repo secret automatically, so it never
  expires unattended. See setup step 8 for what this needs.
- **Update Reel Insights** (daily) — fetches reach/likes/comments/etc. for
  every posted clip and appends them to `clips/insights_log.csv`.

## One-time setup (you need to do this — I can't create accounts or
authenticate as you)

### 1. Convert to an Instagram Business or Creator account
In the Instagram app: Settings → Account type and tools → switch to
Professional account, then Business (or Creator). No Facebook Page linking
is needed for this flow.

### 2. Create a Meta developer app
- Go to https://developers.facebook.com/apps → Create App → type "Business".
- Add the **Instagram** product, and choose the **Access the Instagram API
  with Instagram Login** use case.
- Under the product's API setup, add your Instagram account as a tester and
  accept the invite from within the Instagram app
  (Settings → Apps and websites → Tester invites).

### 3. Authorize the app and get a short-lived token
- Build an authorization URL:
  `https://www.instagram.com/oauth/authorize?client_id=YOUR_APP_ID&redirect_uri=YOUR_REDIRECT_URI&response_type=code&scope=instagram_business_basic,instagram_business_content_publish`
- Visiting it as the account owner and approving returns a `code` on your
  redirect URI.
- Exchange the code for a short-lived token:
  ```
  curl -X POST https://api.instagram.com/oauth/access_token \
    -F client_id=YOUR_APP_ID \
    -F client_secret=YOUR_APP_SECRET \
    -F grant_type=authorization_code \
    -F redirect_uri=YOUR_REDIRECT_URI \
    -F code=THE_CODE
  ```

### 4. Exchange for a long-lived token (~60 days)
```
curl -G https://graph.instagram.com/access_token \
  -d grant_type=ig_exchange_token \
  -d client_secret=YOUR_APP_SECRET \
  -d access_token=SHORT_LIVED_TOKEN
```
This is the value for the `IG_ACCESS_TOKEN` secret. It needs refreshing
before it expires — run `python scripts/refresh_token.py` (with
`IG_ACCESS_TOKEN` set) any time after the token is 24h old and update the
secret with the new value it prints. Do this at least every ~55 days.

### 5. Get your Instagram user ID
```
curl "https://graph.instagram.com/v21.0/me?fields=user_id,username&access_token=YOUR_LONG_LIVED_TOKEN"
```
The `user_id` field is the value for `IG_USER_ID`.

### 6. Create a free Cloudinary account
https://cloudinary.com/users/register/free — grab your Cloud Name, API Key,
and API Secret from the dashboard.

### 7. Add GitHub Actions repository secrets
Repo → Settings → Secrets and variables → Actions → New repository secret,
for each of:

- `IG_ACCESS_TOKEN`
- `IG_USER_ID`
- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`

### 8. Create a `GH_PAT` secret (only needed for automatic token refresh)
The **Refresh Instagram Token** workflow updates the `IG_ACCESS_TOKEN`
secret itself, but the default Actions token can't modify repo secrets —
that needs a personal access token with elevated permission:

1. GitHub → your profile picture → **Settings** → **Developer settings** →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new token**
2. Set **Repository access** to only this repo (`xLegenDon/Clips`)
3. Under **Permissions → Repository permissions**, set **Secrets** to
   **Read and write**
4. Generate it, copy the token, and add it as a repo secret named `GH_PAT`
   (same place as the other secrets)

This token is more sensitive than the others (it can write secrets on this
repo), so scope it to this repo only and treat it carefully. If you'd
rather skip this, don't create `GH_PAT` — the refresh workflow will just
fail harmlessly each week, and you can keep refreshing manually with
`scripts/refresh_token.py` instead.

### 9. Upload clips
Add video files to `clips/pending/` (via a normal commit/PR, or the GitHub
web UI) and push to `main`.

### 10. Merge this branch to `main`
The workflows only run from the default branch's schedule. Once merged,
`daily-post.yml` fires every 2 hours (edit the cron to change timing), the
token refresh runs weekly, and insights are updated daily. Any of them can
also be triggered manually from the Actions tab ("Run workflow").

## Local testing

```
pip install -r requirements.txt
cp .env.example .env   # fill in values
export $(grep -v '^#' .env | xargs)
python scripts/post_daily_clips.py
```

## Notes and limits

- Instagram's Content Publishing API caps an account at 25 posts per rolling
  24 hours; posting 10/day is well within that.
- Long-lived tokens expire after ~60 days. With `GH_PAT` configured, the
  weekly refresh workflow handles this automatically. Without it, run
  `scripts/refresh_token.py` manually and update the `IG_ACCESS_TOKEN`
  secret, or posting will silently start failing once it expires.
- Reels requirements (as of this writing): MP4/MOV, H.264 video, AAC audio,
  9:16 recommended aspect ratio, up to ~1GB / several minutes. Check Meta's
  current docs if a clip fails to process.
- The workflow commits moved files back to the repo after each run, so
  `clips/pending/` only ever holds unposted clips.
- `clips/posted_log.csv` records every posted clip's media ID, which
  `scripts/update_insights.py` uses to look up metrics — don't delete it.
- `clips/insights_log.csv` accumulates a time-series of metrics per clip
  (each daily run appends a new row per clip, so you can track growth over
  time, not just a snapshot).
