#!/usr/bin/env python3
"""Post up to DAILY_POST_LIMIT pending clips to Instagram as Reels."""

import csv
import json
import os
import sys
import time
from pathlib import Path

import cloudinary
import cloudinary.uploader
import requests

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.instagram.com/{GRAPH_API_VERSION}"

PENDING_DIR = Path("clips/pending")
POSTED_DIR = Path("clips/posted")
POSTED_LOG_PATH = Path("clips/posted_log.csv")
HASHTAGS_PATH = Path("hashtags.txt")
VIDEO_EXTENSIONS = {".mp4", ".mov"}

DAILY_POST_LIMIT = int(os.environ.get("DAILY_POST_LIMIT", "10"))
CONTAINER_POLL_INTERVAL_SECONDS = 5
CONTAINER_POLL_TIMEOUT_SECONDS = 300


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def configure_cloudinary() -> None:
    cloudinary.config(
        cloud_name=require_env("CLOUDINARY_CLOUD_NAME"),
        api_key=require_env("CLOUDINARY_API_KEY"),
        api_secret=require_env("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def load_caption(clip_path: Path) -> str:
    caption_path = clip_path.with_suffix(".txt")
    caption = caption_path.read_text(encoding="utf-8").strip() if caption_path.exists() else ""
    hashtags = HASHTAGS_PATH.read_text(encoding="utf-8").strip() if HASHTAGS_PATH.exists() else ""
    if caption and hashtags:
        return f"{caption}\n\n{hashtags}"
    return caption or hashtags


def log_posted_clip(clip_path: Path, media_id: str) -> None:
    is_new = not POSTED_LOG_PATH.exists()
    with POSTED_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["date", "clip", "media_id"])
        writer.writerow([time.strftime("%Y-%m-%d"), clip_path.name, media_id])


def select_pending_clips(limit: int) -> list[Path]:
    clips = sorted(
        p for p in PENDING_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    return clips[:limit]


def upload_to_cloudinary(clip_path: Path) -> str:
    result = cloudinary.uploader.upload_large(
        str(clip_path),
        resource_type="video",
        folder="ig_clips",
    )
    return result["secure_url"]


def create_media_container(ig_user_id: str, access_token: str, video_url: str, caption: str) -> str:
    resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": access_token,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def wait_for_container_ready(creation_id: str, access_token: str) -> None:
    deadline = time.monotonic() + CONTAINER_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{GRAPH_API_BASE}/{creation_id}",
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Container {creation_id} failed processing: {resp.json()}")
        time.sleep(CONTAINER_POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Container {creation_id} did not finish processing in time")


def publish_container(ig_user_id: str, access_token: str, creation_id: str) -> str:
    resp = requests.post(
        f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def archive_clip(clip_path: Path, caption_path: Path) -> None:
    dest_dir = POSTED_DIR / time.strftime("%Y-%m-%d")
    dest_dir.mkdir(parents=True, exist_ok=True)
    clip_path.rename(dest_dir / clip_path.name)
    if caption_path.exists():
        caption_path.rename(dest_dir / caption_path.name)


def main() -> int:
    ig_user_id = require_env("IG_USER_ID")
    access_token = require_env("IG_ACCESS_TOKEN")
    configure_cloudinary()

    clips = select_pending_clips(DAILY_POST_LIMIT)
    if not clips:
        print("No pending clips found in clips/pending/. Nothing to post.")
        return 0

    print(f"Found {len(clips)} clip(s) to post today.")
    results = []
    for clip_path in clips:
        caption_path = clip_path.with_suffix(".txt")
        caption = load_caption(clip_path)
        try:
            print(f"Uploading {clip_path.name} to Cloudinary...")
            video_url = upload_to_cloudinary(clip_path)

            print(f"Creating IG media container for {clip_path.name}...")
            creation_id = create_media_container(ig_user_id, access_token, video_url, caption)

            print(f"Waiting for container {creation_id} to finish processing...")
            wait_for_container_ready(creation_id, access_token)

            print(f"Publishing {clip_path.name}...")
            media_id = publish_container(ig_user_id, access_token, creation_id)

            print(f"Posted {clip_path.name} -> media id {media_id}")
            log_posted_clip(clip_path, media_id)
            archive_clip(clip_path, caption_path)
            results.append({"clip": clip_path.name, "status": "posted", "media_id": media_id})
        except Exception as exc:  # noqa: BLE001 - report and continue with remaining clips
            print(f"Failed to post {clip_path.name}: {exc}", file=sys.stderr)
            results.append({"clip": clip_path.name, "status": "failed", "error": str(exc)})

    posted = sum(1 for r in results if r["status"] == "posted")
    print(f"Done. Posted {posted}/{len(clips)} clip(s).")
    print(json.dumps(results, indent=2))

    return 0 if posted == len(clips) else 1


if __name__ == "__main__":
    raise SystemExit(main())
