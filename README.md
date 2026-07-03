# Clips — Daily Instagram Reel Poster

Automatically posts up to 10 clips a day from `clips/pending/` to Instagram as Reels,
using the official Instagram Graph API (Content Publishing). A GitHub Actions
workflow runs the script once a day.

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

Instagram's Graph API requires each video to be reachable at a public HTTPS
URL — it cannot accept a direct file upload. Cloudinary's free tier is used
to get that public URL without needing to make this repo public.

## One-time setup (you need to do this — I can't create accounts or
authenticate as you)

### 1. Convert to an Instagram Business or Creator account
In the Instagram app: Settings → Account type and tools → switch to
Professional account, then Business (or Creator).

### 2. Link it to a Facebook Page
The Graph API publishes through a Facebook Page connected to the Instagram
account. Instagram app → Settings → Linked accounts → Facebook.

### 3. Create a Meta developer app
- Go to https://developers.facebook.com/apps → Create App → type "Business".
- Add the **Instagram Graph API** product to the app.

### 4. Generate a long-lived access token
- In Graph API Explorer (https://developers.facebook.com/tools/explorer/),
  select your app, and request these permissions: `instagram_basic`,
  `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`.
- Generate a User Access Token, then exchange it for a long-lived token
  (~60 days) via the `oauth/access_token` endpoint with `grant_type=fb_exchange_token`.
- For posting to keep working past 60 days, plan to refresh the token
  periodically (Meta's long-lived tokens are refreshable before they expire).

### 5. Get your Instagram Business Account ID
Call `GET /me/accounts` with your token to list your Pages, then
`GET /<PAGE_ID>?fields=instagram_business_account` to get the IG user ID.

### 6. Create a free Cloudinary account
https://cloudinary.com/users/register/free — grab your Cloud Name, API Key,
and API Secret from the dashboard.

### 7. Add GitHub Actions repository secrets
Repo → Settings → Secrets and variables → Actions → New repository secret,
for each of:

- `IG_ACCESS_TOKEN`
- `IG_BUSINESS_ACCOUNT_ID`
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
- Reels requirements (as of this writing): MP4/MOV, H.264 video, AAC audio,
  9:16 recommended aspect ratio, up to ~1GB / several minutes. Check Meta's
  current docs if a clip fails to process.
- The workflow commits moved files back to the repo after each run, so
  `clips/pending/` only ever holds unposted clips.
