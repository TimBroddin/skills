#!/usr/bin/env python3
"""Check dependencies for the research-yt skill.

Verifies yt-dlp is present and detects which Whisper flavor is available.
Prints a JSON report on stdout. Exits 0 if yt-dlp is found (the only hard
requirement); exits 1 otherwise.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys


def which_version(cmd: str) -> str | None:
    path = shutil.which(cmd)
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        first_line = (out.stdout or out.stderr).strip().splitlines()
        return first_line[0] if first_line else path
    except Exception:
        return path


def detect_whisper() -> dict:
    """Return the preferred Whisper flavor, or None.

    Preference order: mlx-whisper (fastest on Apple Silicon) > whisper.cpp > openai-whisper.
    """
    candidates = []

    if importlib.util.find_spec("mlx_whisper") is not None:
        candidates.append({
            "flavor": "mlx-whisper",
            "kind": "python",
            "invoke": [sys.executable, "-m", "mlx_whisper"],
        })
    elif shutil.which("mlx_whisper"):
        candidates.append({
            "flavor": "mlx-whisper",
            "kind": "cli",
            "invoke": ["mlx_whisper"],
        })

    for name in ("whisper-cli", "whisper-cpp", "whisper.cpp"):
        if shutil.which(name):
            candidates.append({
                "flavor": "whisper.cpp",
                "kind": "cli",
                "invoke": [name],
            })
            break

    if shutil.which("whisper"):
        candidates.append({
            "flavor": "openai-whisper",
            "kind": "cli",
            "invoke": ["whisper"],
        })
    elif importlib.util.find_spec("whisper") is not None:
        candidates.append({
            "flavor": "openai-whisper",
            "kind": "python",
            "invoke": [sys.executable, "-m", "whisper"],
        })

    return {
        "preferred": candidates[0] if candidates else None,
        "available": candidates,
    }


def main() -> int:
    report = {
        "yt_dlp": which_version("yt-dlp"),
        "ffmpeg": which_version("ffmpeg"),
        "whisper": detect_whisper(),
        "python": sys.version.split()[0],
    }

    report["ok"] = report["yt_dlp"] is not None
    report["warnings"] = []
    if report["whisper"]["preferred"] is None:
        report["warnings"].append(
            "No Whisper installation detected. Videos without subtitles will be skipped. "
            "Install one of: mlx-whisper (pip), whisper.cpp (brew install whisper-cpp), or openai-whisper (pip)."
        )
    if report["ffmpeg"] is None:
        report["warnings"].append("ffmpeg not found — required for Whisper audio decoding.")

    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
