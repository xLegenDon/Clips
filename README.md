# Clips — Daily Instagram Reel Poster

Automatically posts up to 10 clips a day from `clips/pending/` to Instagram as Reels,
using the **Instagram API with Instagram Login** (Content Publishing). A GitHub
Actions workflow runs the script once a day.

## How it works

1. Drop video files (`.mp4` or `.mov`) into `clips/pending/`.
2. Optionally add a caption for a clip by creating a same-named `.txt` file
   next to it, e.g. `clip_01.mp4` + `clip_01.txt`.
3. Once a day, GitHub Actions runs `scripts/post_daily_clips.py`, which:
   - picks up to `DAILY_POST_LIMIT` (default 10) pending clips, oldest filename first
   - uploads each clip to Cloudinary to get a public video URL
   - creates an Instagram Reels media container via the Graph API
   - waits for Instagram to finish processing the video
   - publishes it
   - moves the posted clip (and caption) into `clips/posted/<date>/`

Instagram's API requires each video to be reachable at a public HTTPS
URL — it cannot accept a direct file upload. Cloudinary's free tier is used
to get that public URL without needing to make this repo public.

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

### 8. Upload clips
Add video files to `clips/pending/` (via a normal commit/PR, or the GitHub
web UI) and push to `main`.

### 9. Merge this branch to `main`
The workflow only runs from the default branch's schedule. Once merged, it
fires daily at 16:00 UTC (edit the cron in
`.github/workflows/daily-post.yml` to change the time), or can be triggered
manually from the Actions tab ("Run workflow").

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
- Long-lived tokens expire after ~60 days. Run `scripts/refresh_token.py`
  periodically and update the `IG_ACCESS_TOKEN` secret, or posting will
  silently start failing once it expires.
- Reels requirements (as of this writing): MP4/MOV, H.264 video, AAC audio,
  9:16 recommended aspect ratio, up to ~1GB / several minutes. Check Meta's
  current docs if a clip fails to process.
- The workflow commits moved files back to the repo after each run, so
  `clips/pending/` only ever holds unposted clips.
