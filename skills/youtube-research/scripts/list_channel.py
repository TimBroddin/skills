#!/usr/bin/env python3
"""List one or more YouTube channels' videos with titles + descriptions.

Used as the first stage of LLM-driven topic filtering: produces a JSON array
of candidates that the calling Claude session can scan to decide which
videos to actually transcribe.

Caches the index per-channel at <kb>/channels/<slug>/index.json so that
repeated runs don't re-hit yt-dlp for already-known videos.

Single-channel output (one --channel passed):
    {"channel_slug", "channel", "count", "videos": [...]}

Multi-channel output (multiple --channel passed, or comma-separated):
    {"channels": [{"channel_slug", "channel", "count", "videos": [...]}, ...],
     "total_videos": N}

Each video has: id, title, description, duration, upload_date, url, channel_slug.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def slugify(s: str) -> str:
    """Slugify a channel handle, URL, or title.

    For YouTube URLs, extract the handle/channel-id rather than slugifying the whole URL.
    Examples:
      "@veritasium"                                    -> "veritasium"
      "https://www.youtube.com/@StarterStoryBuild"     -> "starterstorybuild"
      "https://www.youtube.com/channel/UCxxxx"         -> "ucxxxx"
      "https://www.youtube.com/c/Veritasium/videos"    -> "veritasium"
    """
    raw = s.strip()
    m = re.search(r"youtube\.com/(?:@|c/|user/|channel/)([^/?#]+)", raw, re.IGNORECASE)
    if m:
        raw = m.group(1)
    raw = raw.lower().lstrip("@").strip()
    raw = re.sub(r"[^a-z0-9]+", "-", raw)
    return raw.strip("-") or "channel"


def channel_url(channel: str) -> str:
    if channel.startswith("http"):
        return channel
    return f"https://www.youtube.com/@{channel.lstrip('@')}/videos"


def list_video_ids(channel: str, limit: int) -> list[dict]:
    """Cheap flat-playlist call: id + title only. Newest first."""
    proc = subprocess.run([
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end", str(limit),
        "--dump-json",
        channel_url(channel),
    ], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp list failed: {proc.stderr.strip()}")
    out = []
    for line in proc.stdout.splitlines():
        try:
            v = json.loads(line)
        except json.JSONDecodeError:
            continue
        if v.get("id"):
            out.append({
                "id": v["id"],
                "title": v.get("title"),
                "duration": v.get("duration"),
            })
    return out


def fetch_full_meta(video_id: str) -> dict | None:
    """Per-video --dump-json (~1s) to pull title + description + upload_date."""
    proc = subprocess.run([
        "yt-dlp", "--dump-json", "--no-download",
        f"https://www.youtube.com/watch?v={video_id}",
    ], capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"  [warn] meta failed for {video_id}: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else '?'}")
        return None
    try:
        v = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return {
        "id": v.get("id"),
        "title": v.get("title"),
        "description": (v.get("description") or "").strip(),
        "duration": v.get("duration"),
        "upload_date": v.get("upload_date"),
        "url": v.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
        "channel": v.get("channel"),
        "channel_id": v.get("channel_id"),
        "channel_url": v.get("channel_url"),
    }


def process_channel(channel_input: str, kb: Path, limit: int, refresh: bool, max_desc: int) -> dict:
    """List + cache one channel. Returns the public summary dict for stdout."""
    slug = slugify(channel_input)
    channel_dir = kb / "channels" / slug
    channel_dir.mkdir(parents=True, exist_ok=True)
    index_path = channel_dir / "index.json"

    cached: dict[str, dict] = {}
    if index_path.exists() and not refresh:
        try:
            cached = {v["id"]: v for v in json.loads(index_path.read_text())["videos"]}
            log(f"[{slug}] [cache] {len(cached)} videos in existing index")
        except Exception:
            cached = {}

    log(f"[{slug}] [list] fetching newest {limit} ids")
    ids = list_video_ids(channel_input, limit)
    log(f"[{slug}] [list] got {len(ids)} ids")

    enriched: list[dict] = []
    new_count = 0
    for i, item in enumerate(ids, 1):
        vid = item["id"]
        if vid in cached and not refresh:
            enriched.append(cached[vid])
            continue
        log(f"[{slug}]   [meta {i}/{len(ids)}] {vid} {item.get('title','')[:60]}")
        meta = fetch_full_meta(vid)
        if meta:
            enriched.append(meta)
            new_count += 1

    channel_meta = next((v for v in enriched if v.get("channel")), None)
    index = {
        "input": channel_input,
        "slug": slug,
        "channel": channel_meta.get("channel") if channel_meta else None,
        "channel_id": channel_meta.get("channel_id") if channel_meta else None,
        "channel_url": channel_meta.get("channel_url") if channel_meta else None,
        "count": len(enriched),
        "videos": enriched,
    }
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    log(f"[{slug}] [done] {len(enriched)} videos in index ({new_count} newly fetched)")

    out_videos = [
        {**v, "description": (v.get("description") or "")[:max_desc], "channel_slug": slug}
        for v in enriched
    ]
    return {
        "channel_slug": slug,
        "channel": index["channel"],
        "count": len(out_videos),
        "videos": out_videos,
    }


def expand_channels(raw: list[str]) -> list[str]:
    """Accept repeated --channel flags AND/OR comma-separated values within one flag.
    Preserves order, drops duplicates and blanks.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        for piece in item.split(","):
            piece = piece.strip()
            if piece and piece not in seen:
                seen.add(piece)
                out.append(piece)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="List one or more channels' videos with descriptions for LLM filtering.")
    p.add_argument("--channel", required=True, action="append",
                   help="Channel handle, URL, or @name. Repeat for multiple channels, or pass comma-separated.")
    p.add_argument("--kb", required=True, help="KB root (./.youtube-research)")
    p.add_argument("--limit", type=int, default=50, help="Max videos per channel (newest first)")
    p.add_argument("--refresh", action="store_true", help="Ignore cached index and refetch all metadata")
    p.add_argument("--max-desc", type=int, default=600,
                   help="Truncate descriptions to N chars in output (default 600)")
    args = p.parse_args()

    if not shutil.which("yt-dlp"):
        log("error: yt-dlp not found on PATH")
        return 2

    kb = Path(args.kb).resolve()
    channels = expand_channels(args.channel)
    if not channels:
        log("error: no channels provided")
        return 2

    results = []
    for ch in channels:
        try:
            results.append(process_channel(ch, kb, args.limit, args.refresh, args.max_desc))
        except Exception as e:
            log(f"[error] channel {ch!r} failed: {e}")
            results.append({
                "channel_slug": slugify(ch),
                "channel": None,
                "count": 0,
                "videos": [],
                "error": str(e),
            })

    if len(results) == 1:
        # Backwards-compatible single-channel output.
        print(json.dumps(results[0], indent=2))
    else:
        total = sum(r["count"] for r in results)
        print(json.dumps({
            "channels": results,
            "total_videos": total,
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
