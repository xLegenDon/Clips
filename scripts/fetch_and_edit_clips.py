#!/usr/bin/env python3
"""Turn source VODs into edited clips: download, transcribe, pick a
highlight with Claude, trim, burn in captions, and drop into
clips/pending/. Sources come from sources.txt (one per line, curated —
see README before adding a channel)."""

import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import anthropic
import yt_dlp
from faster_whisper import WhisperModel

SOURCES_PATH = Path("sources.txt")
PENDING_DIR = Path("clips/pending")
WORK_DIR = Path("clip_pipeline_tmp")
HIGHLIGHT_LENGTH_SECONDS = 30
WHISPER_MODEL_SIZE = "base"
TWITCH_URL_PATTERN = re.compile(r"^https?://(www\.|clips\.|m\.)?twitch\.tv/", re.IGNORECASE)

client = anthropic.Anthropic()


def read_sources() -> list[str]:
    if not SOURCES_PATH.exists():
        return []
    lines = SOURCES_PATH.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def remove_source(url: str) -> None:
    lines = SOURCES_PATH.read_text(encoding="utf-8").splitlines()
    remaining = [line for line in lines if line.strip() != url]
    SOURCES_PATH.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")


def validate_source(url: str) -> str:
    """Returns an error message, or an empty string if the source is clear to process."""
    if not TWITCH_URL_PATTERN.match(url):
        return "only twitch.tv sources are supported right now"
    return ""


def get_channel_credit(url: str) -> str:
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    channel = info.get("uploader_id") or info.get("uploader") or info.get("channel")
    if not channel:
        raise ValueError(f"Could not determine the channel/uploader for {url}")
    return f"@{channel}"


def download_video(url: str, out_path: Path) -> None:
    ydl_opts = {"format": "mp4/best", "outtmpl": str(out_path), "quiet": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def transcribe(video_path: Path) -> list[dict]:
    model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu")
    segments, _ = model.transcribe(str(video_path), word_timestamps=True)
    return [{"start": seg.start, "end": seg.end, "text": seg.text} for seg in segments]


def pick_highlight(segments: list[dict], target_length: int) -> dict:
    transcript_text = "\n".join(
        f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments
    )
    prompt = f"""Here's a timestamped transcript of a video.
Pick the single most engaging {target_length}-second window for a short-form
vertical clip (funny, exciting, or surprising moment). Respond with ONLY a
JSON object, no other text: {{"start": <seconds>, "end": <seconds>, "reason": "<why>"}}

Transcript:
{transcript_text}
"""
    resp = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Could not find JSON in Claude's response: {text!r}")
    return json.loads(match.group(0))


def trim_clip(input_path: Path, start: float, end: float, out_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-ss", str(start), "-i", str(input_path),
            "-t", str(end - start), "-c:v", "libx264", "-c:a", "aac",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


def format_srt_timestamp(seconds: float) -> str:
    hours, rem = divmod(max(seconds, 0), 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):02}:{int(minutes):02}:{secs:06.3f}".replace(".", ",")


def make_srt(segments: list[dict], start_offset: float, end_offset: float, out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8") as f:
        index = 1
        for seg in segments:
            if seg["end"] < start_offset or seg["start"] > end_offset:
                continue
            s = seg["start"] - start_offset
            e = seg["end"] - start_offset
            f.write(f"{index}\n{format_srt_timestamp(s)} --> {format_srt_timestamp(e)}\n{seg['text'].strip()}\n\n")
            index += 1


def escape_for_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def burn_captions_and_watermark(video_path: Path, srt_path: Path, credit: str, out_path: Path) -> None:
    watermark_text = escape_for_drawtext(f"Clip via {credit}")
    drawtext = (
        f"drawtext=text='{watermark_text}':fontsize=20:fontcolor=white"
        ":box=1:boxcolor=black@0.5:boxborderw=8:x=20:y=h-th-20"
    )
    subtitles = f"subtitles={srt_path}:force_style='FontSize=18,PrimaryColour=&HFFFFFF&'"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", f"{subtitles},{drawtext}",
            "-c:a", "copy", str(out_path),
        ],
        check=True,
        capture_output=True,
    )


def write_caption_file(caption_path: Path, reason: str, credit: str) -> None:
    lines = [reason.strip()]
    if credit:
        lines.append(f"\U0001F3AC Clip via {credit}")
    caption_path.write_text("\n\n".join(lines), encoding="utf-8")


def process_source(url: str) -> None:
    credit = get_channel_credit(url)
    print(f"Processing {url} (credit: {credit})...")

    WORK_DIR.mkdir(exist_ok=True)
    raw_path = WORK_DIR / "raw.mp4"
    trimmed_path = WORK_DIR / "trimmed.mp4"
    srt_path = WORK_DIR / "captions.srt"

    download_video(url, raw_path)
    segments = transcribe(raw_path)
    highlight = pick_highlight(segments, HIGHLIGHT_LENGTH_SECONDS)

    trim_clip(raw_path, highlight["start"], highlight["end"], trimmed_path)
    make_srt(segments, highlight["start"], highlight["end"], srt_path)

    clip_name = uuid.uuid4().hex[:8]
    final_path = PENDING_DIR / f"{clip_name}.mp4"
    burn_captions_and_watermark(trimmed_path, srt_path, credit, final_path)
    write_caption_file(PENDING_DIR / f"{clip_name}.txt", highlight.get("reason", ""), credit)

    for tmp_file in (raw_path, trimmed_path, srt_path):
        tmp_file.unlink(missing_ok=True)

    print(f"Added {clip_name}.mp4 -> {highlight.get('reason', '')}")


def main() -> int:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    sources = read_sources()
    if not sources:
        print("No sources in sources.txt. Nothing to fetch.")
        return 0

    for url in sources:
        error = validate_source(url)
        if error:
            print(f"Skipping {url}: {error} (left in sources.txt — fix and it'll be retried)")
            continue

        try:
            process_source(url)
        except Exception as exc:  # noqa: BLE001 - report and continue with remaining sources
            print(f"Failed to process {url}: {exc}")
        finally:
            remove_source(url)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
