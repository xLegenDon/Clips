#!/usr/bin/env python3
"""Refresh the Instagram access token and push the new value into the repo's
GH_PAT-authenticated GitHub Actions secret, so it never expires unattended."""

import os

import requests
from nacl import encoding, public


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def refresh_ig_token(current_token: str) -> str:
    resp = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": current_token},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"Refreshed token, expires in {data['expires_in']} seconds.")
    return data["access_token"]


def update_github_secret(repo: str, github_token: str, secret_name: str, secret_value: str) -> None:
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }
    key_resp = requests.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=headers,
        timeout=30,
    )
    key_resp.raise_for_status()
    key_data = key_resp.json()

    public_key = public.PublicKey(key_data["key"].encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key)
    encrypted_value = sealed_box.encrypt(secret_value.encode("utf-8"), encoding.Base64Encoder).decode("utf-8")

    put_resp = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}",
        headers=headers,
        json={"encrypted_value": encrypted_value, "key_id": key_data["key_id"]},
        timeout=30,
    )
    put_resp.raise_for_status()
    print(f"Updated repo secret {secret_name}.")


def main() -> int:
    current_token = require_env("IG_ACCESS_TOKEN")
    repo = require_env("GITHUB_REPOSITORY")
    github_token = require_env("GH_PAT")

    new_token = refresh_ig_token(current_token)
    update_github_secret(repo, github_token, "IG_ACCESS_TOKEN", new_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
