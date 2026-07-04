#!/usr/bin/env python3
"""Fetch Reels insights for every previously posted clip and log them."""

import csv
import os
import time
from pathlib import Path

import requests

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.instagram.com/{GRAPH_API_VERSION}"

POSTED_LOG_PATH = Path("clips/posted_log.csv")
INSIGHTS_LOG_PATH = Path("clips/insights_log.csv")
METRICS = ["reach", "likes", "comments", "shares", "saved", "plays", "total_interactions"]


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def read_posted_clips() -> list[dict]:
    if not POSTED_LOG_PATH.exists():
        return []
    with POSTED_LOG_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_insights(media_id: str, access_token: str) -> dict:
    resp = requests.get(
        f"{GRAPH_API_BASE}/{media_id}/insights",
        params={"metric": ",".join(METRICS), "access_token": access_token},
        timeout=30,
    )
    resp.raise_for_status()
    values = {}
    for entry in resp.json().get("data", []):
        name = entry.get("name")
        entry_values = entry.get("values", [])
        values[name] = entry_values[0]["value"] if entry_values else None
    return values


def main() -> int:
    access_token = require_env("IG_ACCESS_TOKEN")

    posted_clips = read_posted_clips()
    if not posted_clips:
        print("No posted clips logged yet. Nothing to fetch insights for.")
        return 0

    is_new = not INSIGHTS_LOG_PATH.exists()
    with INSIGHTS_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["checked_at", "clip", "media_id", *METRICS])

        for row in posted_clips:
            media_id = row["media_id"]
            try:
                values = fetch_insights(media_id, access_token)
                writer.writerow(
                    [time.strftime("%Y-%m-%d %H:%M:%S"), row["clip"], media_id]
                    + [values.get(metric, "") for metric in METRICS]
                )
                print(f"Logged insights for {row['clip']} ({media_id}).")
            except requests.HTTPError as exc:
                print(f"Skipping {row['clip']} ({media_id}): {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
