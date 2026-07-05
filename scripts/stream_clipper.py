#!/usr/bin/env python3
"""
Stream Clipper — post-stream VOD → Instagram-ready vertical clips.

Runs on your own machine (not GitHub Actions — it needs access to your local
OBS recordings). Output drops straight into a clips/pending/-compatible
format: <clip>.mp4 + <clip>.txt per clip, so point CLIP_OUTPUT_DIR at your
local clone's clips/pending/ and the existing GitHub-hosted posting
pipeline takes over once you push.

Flow:
  1. Extract audio from the recording (ffmpeg)
  2. Transcribe with faster-whisper (local, timestamped)
  3. Ask Claude to pick the most clip-worthy moments from the transcript
  4. Cut each clip and convert to 1080x1920 vertical (ffmpeg)
  5. Drop clips + caption .txt files into the output folder your IG pipeline watches

Usage:
  python stream_clipper.py /path/to/recording.mkv
  python stream_clipper.py --watch /path/to/obs/recordings   # process new files as they appear

Env:
  ANTHROPIC_API_KEY must be set.
  CLIP_OUTPUT_DIR: where finished clips land (default ./clips_out). Point
    this at your local clone's clips/pending/ to feed the posting pipeline.
  GIT_REPO_DIR: if set, auto-commits and pushes clips/pending/ after each
    VOD is processed. Leave unset to just write files locally.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — tweak these to taste
# ---------------------------------------------------------------------------

NUM_CLIPS = 4                 # how many clips to produce per VOD
MIN_CLIP_SEC = 20             # clip length bounds
MAX_CLIP_SEC = 75
PAD_BEFORE_SEC = 3.0          # context added before/after the chosen moment
PAD_AFTER_SEC = 2.0
WHISPER_MODEL = "small"       # tiny / base / small / medium / large-v3
WHISPER_DEVICE = "auto"       # "cuda" if you have an NVIDIA GPU, else "cpu"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"  # cheap + fast; use claude-sonnet-5 for better picks
VERTICAL_MODE = "blurpad"     # "blurpad" (full frame on blurred bg) or "centercrop"
OUTPUT_DIR = Path(os.environ.get("CLIP_OUTPUT_DIR", "./clips_out"))  # your IG pipeline watches this
GIT_REPO_DIR = os.environ.get("GIT_REPO_DIR")  # set to auto-commit+push after each VOD
PROCESSED_LOG = Path("./.processed_vods.json")
VIDEO_EXTS = {".mkv", ".mp4", ".mov", ".flv", ".ts"}
STABLE_SECONDS = 30           # watch mode: file must be unchanged this long (recording finished)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str]) -> None:
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def extract_audio(video: Path, wav: Path) -> None:
    print(f"[1/4] Extracting audio from {video.name}")
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav)])


def transcribe(wav: Path) -> list[dict]:
    """Returns [{'start': float, 'end': float, 'text': str}, ...]"""
    print(f"[2/4] Transcribing with faster-whisper ({WHISPER_MODEL}) — this is the slow part")
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type="auto")
    segments, _info = model.transcribe(str(wav), vad_filter=True)
    out = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            out.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
    print(f"      {len(out)} transcript segments")
    return out


def format_transcript(segments: list[dict]) -> str:
    lines = []
    for s in segments:
        m, sec = divmod(int(s["start"]), 60)
        h, m = divmod(m, 60)
        lines.append(f"[{h:02d}:{m:02d}:{sec:02d} @ {s['start']}] {s['text']}")
    return "\n".join(lines)


def pick_moments(segments: list[dict], video_duration: float) -> list[dict]:
    """Ask Claude for the best clip moments. Returns list of
    {'start': float, 'end': float, 'title': str, 'caption': str}."""
    print(f"[3/4] Scoring transcript with Claude ({CLAUDE_MODEL})")
    import anthropic

    transcript = format_transcript(segments)
    # Very long VODs: keep request under control by chunking
    chunks = chunk_text(transcript, max_chars=90_000)
    candidates: list[dict] = []

    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY
    for i, chunk in enumerate(chunks, 1):
        prompt = f"""You are selecting short-form clips from a live stream transcript for Instagram Reels.

Below is a timestamped transcript (timestamps are in seconds after the @ sign).
Pick up to {NUM_CLIPS + 2} moments that would make great standalone clips: jokes, hype moments,
hot takes, big reactions, satisfying payoffs, or self-contained stories. Each clip must make
sense WITHOUT surrounding context, be {MIN_CLIP_SEC}-{MAX_CLIP_SEC} seconds long, and start
slightly BEFORE the key line so the viewer gets setup.

Respond ONLY with a JSON array, no markdown fences, no commentary:
[{{"start": <seconds float>, "end": <seconds float>, "score": <1-10>,
   "title": "<short punchy filename-safe title>",
   "caption": "<instagram caption with a hook, no hashtags>"}}]

Transcript chunk {i}/{len(chunks)}:
{chunk}"""
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        text = re.sub(r"```(json)?", "", text).strip()
        try:
            candidates.extend(json.loads(text))
        except json.JSONDecodeError:
            print(f"      warning: could not parse model output for chunk {i}, skipping")

    # sanitize, dedupe overlaps, keep top N by score
    cleaned = []
    for c in candidates:
        try:
            start = max(0.0, float(c["start"]) - PAD_BEFORE_SEC)
            end = min(video_duration, float(c["end"]) + PAD_AFTER_SEC)
        except (KeyError, TypeError, ValueError):
            continue
        dur = end - start
        if dur < MIN_CLIP_SEC:
            end = min(video_duration, start + MIN_CLIP_SEC)
        if end - start > MAX_CLIP_SEC:
            end = start + MAX_CLIP_SEC
        if end - start < MIN_CLIP_SEC * 0.6:
            continue
        cleaned.append({
            "start": start, "end": end,
            "score": float(c.get("score", 5)),
            "title": slugify(str(c.get("title", "clip"))),
            "caption": str(c.get("caption", "")),
        })

    cleaned.sort(key=lambda c: -c["score"])
    picked: list[dict] = []
    for c in cleaned:
        if any(overlap(c, p) for p in picked):
            continue
        picked.append(c)
        if len(picked) >= NUM_CLIPS:
            break
    picked.sort(key=lambda c: c["start"])
    print(f"      picked {len(picked)} clips")
    return picked


def overlap(a: dict, b: dict) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def chunk_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    lines, chunks, cur = text.split("\n"), [], []
    size = 0
    for line in lines:
        if size + len(line) > max_chars and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:60] or "clip"


def video_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def cut_clip(video: Path, clip: dict, out_path: Path) -> None:
    start, dur = clip["start"], clip["end"] - clip["start"]
    if VERTICAL_MODE == "centercrop":
        vf = "crop=ih*9/16:ih,scale=1080:1920"
    else:  # blurpad: blurred fullscreen bg + full frame centered
        vf = ("split[a][b];"
              "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,gblur=sigma=25[bg];"
              "[b]scale=1080:-2[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2")
    run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(video),
         "-filter_complex" if VERTICAL_MODE != "centercrop" else "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)])


def push_to_repo(repo_dir: str) -> None:
    print(f"Committing and pushing clips/pending/ in {repo_dir}")
    subprocess.run(["git", "-C", repo_dir, "add", "clips/pending"], check=True)
    result = subprocess.run(["git", "-C", repo_dir, "diff", "--cached", "--quiet"])
    if result.returncode == 0:
        print("      nothing new to commit")
        return
    subprocess.run(["git", "-C", repo_dir, "commit", "-m", "Add stream-clipper clips"], check=True)
    subprocess.run(["git", "-C", repo_dir, "push"], check=True)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_vod(video: Path) -> None:
    print(f"\n=== Processing {video} ===")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    duration = video_duration(video)
    wav = video.with_suffix(".clipper.wav")
    try:
        extract_audio(video, wav)
        segments = transcribe(wav)
    finally:
        wav.unlink(missing_ok=True)

    if not segments:
        print("No speech found — nothing to clip.")
        return

    clips = pick_moments(segments, duration)
    if not clips:
        print("Model returned no usable moments.")
        return

    print("[4/4] Cutting clips")
    stamp = time.strftime("%Y%m%d-%H%M")
    metadata = []
    for i, clip in enumerate(clips, 1):
        name = f"{stamp}_{i:02d}_{clip['title']}"
        out_path = OUTPUT_DIR / f"{name}.mp4"
        cut_clip(video, clip, out_path)
        (OUTPUT_DIR / f"{name}.txt").write_text(clip["caption"], encoding="utf-8")
        metadata.append({
            "file": f"{name}.mp4",
            "source": video.name,
            "start_sec": round(clip["start"], 1),
            "end_sec": round(clip["end"], 1),
            "caption": clip["caption"],
        })
        print(f"      -> {out_path}")

    meta_path = OUTPUT_DIR / f"{stamp}_clips.json"
    meta_path.write_text(json.dumps(metadata, indent=2))
    print(f"Done. {len(metadata)} clips + metadata at {meta_path}")

    if GIT_REPO_DIR:
        push_to_repo(GIT_REPO_DIR)


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def load_processed() -> set[str]:
    if PROCESSED_LOG.exists():
        return set(json.loads(PROCESSED_LOG.read_text()))
    return set()


def save_processed(done: set[str]) -> None:
    PROCESSED_LOG.write_text(json.dumps(sorted(done)))


def watch(folder: Path) -> None:
    print(f"Watching {folder} for finished recordings (Ctrl+C to stop)")
    done = load_processed()
    sizes: dict[str, tuple[int, float]] = {}
    while True:
        for f in folder.iterdir():
            if f.suffix.lower() not in VIDEO_EXTS or str(f) in done:
                continue
            size = f.stat().st_size
            prev = sizes.get(str(f))
            now = time.time()
            if prev is None or prev[0] != size:
                sizes[str(f)] = (size, now)      # still growing (recording)
            elif now - prev[1] >= STABLE_SECONDS:  # stable long enough = finished
                try:
                    process_vod(f)
                except Exception as e:
                    print(f"ERROR processing {f}: {e}")
                done.add(str(f))
                save_processed(done)
        time.sleep(10)


def main() -> None:
    ap = argparse.ArgumentParser(description="Turn stream VODs into vertical clips")
    ap.add_argument("path", type=Path, help="video file, or folder with --watch")
    ap.add_argument("--watch", action="store_true", help="watch folder for new recordings")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first.")
    if args.watch:
        watch(args.path)
    else:
        process_vod(args.path)


if __name__ == "__main__":
    main()
