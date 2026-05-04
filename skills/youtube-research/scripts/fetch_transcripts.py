#!/usr/bin/env python3
"""Fetch transcripts for a YouTube channel or single video into the KB.

For each video:
  1. Skip if <kb>/channels/<slug>/videos/<id>/transcript.md already exists.
  2. Try yt-dlp subtitles (manual → auto-generated).
  3. Fall back to Whisper on the downloaded audio.
  4. Write meta.json + transcript.md.

Streams progress to stderr; emits a JSON summary on stdout.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------- helpers ----------

def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def slugify(s: str) -> str:
    """Slugify a channel handle, URL, or title.

    For YouTube URLs, extract the handle/channel-id rather than slugifying the whole URL.
    """
    raw = s.strip()
    m = re.search(r"youtube\.com/(?:@|c/|user/|channel/)([^/?#]+)", raw, re.IGNORECASE)
    if m:
        raw = m.group(1)
    raw = raw.lower().lstrip("@").strip()
    raw = re.sub(r"[^a-z0-9]+", "-", raw)
    return raw.strip("-") or "channel"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def detect_whisper() -> dict | None:
    """Mirror of check_deps.detect_whisper, returns preferred only."""
    if importlib.util.find_spec("mlx_whisper") is not None:
        return {"flavor": "mlx-whisper", "kind": "python",
                "invoke": [sys.executable, "-m", "mlx_whisper"]}
    if shutil.which("mlx_whisper"):
        return {"flavor": "mlx-whisper", "kind": "cli", "invoke": ["mlx_whisper"]}
    for name in ("whisper-cli", "whisper-cpp", "whisper.cpp"):
        if shutil.which(name):
            return {"flavor": "whisper.cpp", "kind": "cli", "invoke": [name]}
    if shutil.which("whisper"):
        return {"flavor": "openai-whisper", "kind": "cli", "invoke": ["whisper"]}
    if importlib.util.find_spec("whisper") is not None:
        return {"flavor": "openai-whisper", "kind": "python",
                "invoke": [sys.executable, "-m", "whisper"]}
    return None


# ---------- yt-dlp interactions ----------

def list_channel_videos(channel: str, limit: int) -> list[dict]:
    """Return [{id, title, upload_date, duration, webpage_url}] newest first."""
    url = channel
    if not channel.startswith("http"):
        handle = channel.lstrip("@")
        url = f"https://www.youtube.com/@{handle}/videos"

    log(f"[list] fetching up to {limit} videos from {url}")
    proc = run([
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end", str(limit),
        "--dump-json",
        url,
    ])
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp list failed: {proc.stderr.strip()}")

    videos = []
    for line in proc.stdout.splitlines():
        try:
            v = json.loads(line)
        except json.JSONDecodeError:
            continue
        videos.append({
            "id": v.get("id"),
            "title": v.get("title"),
            "upload_date": v.get("upload_date"),
            "duration": v.get("duration"),
            "url": v.get("url") or v.get("webpage_url") or f"https://www.youtube.com/watch?v={v.get('id')}",
        })
    return videos


def get_video_meta(video_url: str) -> dict:
    proc = run(["yt-dlp", "--dump-json", "--no-download", video_url])
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp meta failed: {proc.stderr.strip()}")
    v = json.loads(proc.stdout)
    return {
        "id": v.get("id"),
        "title": v.get("title"),
        "upload_date": v.get("upload_date"),
        "duration": v.get("duration"),
        "url": v.get("webpage_url") or video_url,
        "channel": v.get("channel"),
        "channel_id": v.get("channel_id"),
        "channel_url": v.get("channel_url"),
    }


# ---------- subtitle/whisper handling ----------

VTT_TIMECODE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}\.\d{3}")
VTT_TAG = re.compile(r"<[^>]+>")


def parse_vtt(vtt_text: str) -> str:
    """Convert WebVTT to plain text with [HH:MM:SS] anchors per cue.

    De-duplicates consecutive identical lines (auto-subs repeat heavily).
    """
    out_lines: list[str] = []
    last_text = None
    current_ts: str | None = None
    for raw in vtt_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        m = VTT_TIMECODE.match(line)
        if m:
            current_ts = line.split(" ")[0].split(".")[0]  # HH:MM:SS
            continue
        if "-->" in line:
            continue
        text = VTT_TAG.sub("", line).strip()
        if not text or text == last_text:
            continue
        if current_ts:
            out_lines.append(f"[{current_ts}] {text}")
            current_ts = None
        else:
            out_lines.append(text)
        last_text = text
    return "\n".join(out_lines)


def try_fetch_subs(video_url: str, work_dir: Path) -> tuple[str, str] | None:
    """Try to fetch subs via yt-dlp. Returns (transcript_text, source) or None.

    source is "subs" (manual) or "auto-subs" (auto-generated).
    """
    out_template = str(work_dir / "%(id)s.%(ext)s")

    for mode, source_label in (("--write-subs", "subs"), ("--write-auto-subs", "auto-subs")):
        proc = run([
            "yt-dlp",
            "--skip-download",
            mode,
            "--sub-langs", "en.*,en",
            "--sub-format", "vtt",
            "-o", out_template,
            video_url,
        ])
        # yt-dlp succeeds even when no subs exist; check for vtt files
        vtts = sorted(work_dir.glob("*.vtt"))
        if vtts:
            text = parse_vtt(vtts[0].read_text(encoding="utf-8", errors="replace"))
            if text.strip():
                return text, source_label
            for f in vtts:
                f.unlink(missing_ok=True)
    return None


def transcribe_with_whisper(video_url: str, work_dir: Path, whisper: dict) -> str:
    """Download audio with yt-dlp and transcribe with the detected Whisper."""
    audio_template = str(work_dir / "audio.%(ext)s")
    log(f"  [whisper] downloading audio…")
    proc = run([
        "yt-dlp",
        "-f", "bestaudio",
        "-x", "--audio-format", "mp3",
        "-o", audio_template,
        video_url,
    ])
    if proc.returncode != 0:
        raise RuntimeError(f"audio download failed: {proc.stderr.strip()}")
    audio = next(work_dir.glob("audio.*"), None)
    if not audio:
        raise RuntimeError("audio file missing after yt-dlp")

    log(f"  [whisper] transcribing with {whisper['flavor']}…")
    flavor = whisper["flavor"]
    invoke = whisper["invoke"]
    out_dir = work_dir / "whisper"
    out_dir.mkdir(exist_ok=True)

    if flavor == "mlx-whisper":
        cmd = invoke + [str(audio), "--output-dir", str(out_dir),
                        "--output-format", "vtt", "--model", "mlx-community/whisper-small-mlx"]
    elif flavor == "whisper.cpp":
        # whisper-cli expects a GGML model via -m; user must have one. Fall back to default search.
        cmd = invoke + ["-f", str(audio), "-ovtt", "-of", str(out_dir / audio.stem)]
    else:  # openai-whisper
        cmd = invoke + [str(audio), "--output_dir", str(out_dir),
                        "--output_format", "vtt", "--model", "small"]

    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"whisper failed: {proc.stderr.strip() or proc.stdout.strip()}")

    vtt = next(out_dir.glob("*.vtt"), None)
    if not vtt:
        raise RuntimeError("whisper produced no .vtt output")
    return parse_vtt(vtt.read_text(encoding="utf-8", errors="replace"))


# ---------- per-video processing ----------

def process_video(video_url: str, channel_dir: Path, whisper: dict | None,
                  force_whisper: bool) -> dict:
    meta = get_video_meta(video_url)
    vid = meta["id"]
    video_dir = channel_dir / "videos" / vid
    transcript_path = video_dir / "transcript.md"
    meta_path = video_dir / "meta.json"

    if transcript_path.exists():
        log(f"[skip] {vid} {meta['title']!r} (already cached)")
        return {"id": vid, "status": "cached", "title": meta["title"]}

    video_dir.mkdir(parents=True, exist_ok=True)
    log(f"[fetch] {vid} {meta['title']!r}")

    transcript: str | None = None
    source: str | None = None

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if not force_whisper:
            result = try_fetch_subs(video_url, work)
            if result:
                transcript, source = result
                log(f"  [{source}] got {len(transcript)} chars")

        if transcript is None:
            if whisper is None:
                log(f"  [skip] no subs and no whisper available")
                shutil.rmtree(video_dir, ignore_errors=True)
                return {"id": vid, "status": "no-transcript", "title": meta["title"]}
            try:
                transcript = transcribe_with_whisper(video_url, work, whisper)
                source = f"whisper:{whisper['flavor']}"
                log(f"  [whisper] got {len(transcript)} chars")
            except Exception as e:
                log(f"  [error] whisper failed: {e}")
                shutil.rmtree(video_dir, ignore_errors=True)
                return {"id": vid, "status": "error", "error": str(e), "title": meta["title"]}

    meta_out = {**meta, "source": source}
    meta_path.write_text(json.dumps(meta_out, indent=2), encoding="utf-8")

    header = f"# {meta['title']}\n\n- Video: {meta['url']}\n- Uploaded: {meta.get('upload_date','?')}\n- Source: {source}\n\n"
    transcript_path.write_text(header + transcript + "\n", encoding="utf-8")
    return {"id": vid, "status": "ok", "source": source, "title": meta["title"]}


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description="Fetch YouTube transcripts into the youtube-research KB.")
    p.add_argument("--channel", help="Channel handle, URL, or @name (fetches newest --limit videos)")
    p.add_argument("--video", help="Single video URL or ID")
    p.add_argument("--ids", help="Comma-separated video IDs (LLM-filtered set). Requires --channel for slug.")
    p.add_argument("--kb", required=True, help="Path to KB root (./.youtube-research)")
    p.add_argument("--limit", type=int, default=50, help="Max videos when fetching a channel")
    p.add_argument("--force-whisper", action="store_true", help="Skip subs; transcribe with Whisper")
    args = p.parse_args()

    if not args.channel and not args.video and not args.ids:
        p.error("provide --channel, --video, or --ids")
    if args.ids and not args.channel:
        p.error("--ids requires --channel (for KB slug)")

    if not shutil.which("yt-dlp"):
        log("error: yt-dlp not found on PATH")
        return 2

    whisper = detect_whisper()
    if whisper:
        log(f"[deps] whisper: {whisper['flavor']}")
    else:
        log("[deps] whisper: none (videos without subs will be skipped)")

    kb = Path(args.kb).resolve()

    if args.video:
        video_url = args.video if args.video.startswith("http") else f"https://www.youtube.com/watch?v={args.video}"
        meta = get_video_meta(video_url)
        channel_slug = slugify(meta.get("channel") or meta.get("channel_id") or "unknown")
        channel_dir = kb / "channels" / channel_slug
        channel_dir.mkdir(parents=True, exist_ok=True)
        ch_meta = channel_dir / "channel.json"
        if not ch_meta.exists():
            ch_meta.write_text(json.dumps({
                "title": meta.get("channel"),
                "channel_id": meta.get("channel_id"),
                "url": meta.get("channel_url"),
            }, indent=2), encoding="utf-8")
        results = [process_video(video_url, channel_dir, whisper, args.force_whisper)]
    else:
        channel_slug = slugify(args.channel)
        channel_dir = kb / "channels" / channel_slug
        channel_dir.mkdir(parents=True, exist_ok=True)
        if args.ids:
            ids = [x.strip() for x in args.ids.split(",") if x.strip()]
            videos = [{"id": i, "url": f"https://www.youtube.com/watch?v={i}"} for i in ids]
            log(f"[ids] {len(videos)} explicit ids")
        else:
            videos = list_channel_videos(args.channel, args.limit)
            log(f"[list] {len(videos)} videos")
        ch_meta = channel_dir / "channel.json"
        ch_meta.write_text(json.dumps({
            "input": args.channel,
            "slug": channel_slug,
            "video_count_indexed": len(videos),
        }, indent=2), encoding="utf-8")
        results = []
        for v in videos:
            try:
                results.append(process_video(v["url"], channel_dir, whisper, args.force_whisper))
            except Exception as e:
                log(f"[error] {v.get('id')}: {e}")
                results.append({"id": v.get("id"), "status": "error", "error": str(e)})

    summary = {
        "kb": str(kb),
        "channel_slug": channel_slug,
        "total": len(results),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "cached": sum(1 for r in results if r["status"] == "cached"),
        "skipped": sum(1 for r in results if r["status"] == "no-transcript"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
