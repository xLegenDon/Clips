#!/usr/bin/env python3
"""Refresh a long-lived Instagram access token before its ~60-day expiry."""

import os

import requests

GRAPH_API_VERSION = "v21.0"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    access_token = require_env("IG_ACCESS_TOKEN")
    resp = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": access_token},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"New long-lived access token (expires in {data['expires_in']} seconds):")
    print(data["access_token"])
    print("\nUpdate the IG_ACCESS_TOKEN secret in the repo with this value.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
