#!/usr/bin/env python3
"""Render a topic markdown file to PDF.

Tries multiple backends in order:
  1. pandoc + xelatex  (best output, full unicode)
  2. pandoc + lualatex
  3. pandoc + wkhtmltopdf
  4. pandoc -> standalone HTML  (no PDF; user installs something)

Prints the output path on stdout. Exits non-zero on failure.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def try_pandoc(input_md: Path, output: Path, engine: str) -> bool:
    cmd = [
        "pandoc",
        str(input_md),
        "-o", str(output),
        f"--pdf-engine={engine}",
        "-V", "geometry:margin=1in",
        "-V", "linkcolor:blue",
        "-V", "mainfont=Helvetica",
        "--standalone",
    ]
    log(f"[pandoc] trying {engine}…")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and output.exists():
        return True
    log(f"  [{engine}] failed: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'unknown error'}")
    return False


def try_pandoc_wkhtml(input_md: Path, output: Path) -> bool:
    if not shutil.which("wkhtmltopdf"):
        return False
    cmd = [
        "pandoc",
        str(input_md),
        "-o", str(output),
        "--pdf-engine=wkhtmltopdf",
        "-V", "margin-left=20mm", "-V", "margin-right=20mm",
        "-V", "margin-top=20mm", "-V", "margin-bottom=20mm",
        "--standalone",
    ]
    log("[pandoc] trying wkhtmltopdf…")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and output.exists():
        return True
    log(f"  [wkhtmltopdf] failed: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'unknown error'}")
    return False


def render_html_fallback(input_md: Path, output_html: Path) -> bool:
    cmd = [
        "pandoc",
        str(input_md),
        "-o", str(output_html),
        "--standalone",
        "--metadata", f"title={input_md.stem}",
    ]
    log("[pandoc] falling back to standalone HTML…")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and output_html.exists():
        return True
    log(f"  [html] failed: {proc.stderr.strip()}")
    return False


def main() -> int:
    p = argparse.ArgumentParser(description="Render a topic markdown file to PDF.")
    p.add_argument("input", help="Path to the topic markdown file")
    p.add_argument("-o", "--output", help="Output PDF path (defaults to <input>.pdf)")
    args = p.parse_args()

    input_md = Path(args.input).resolve()
    if not input_md.exists():
        log(f"error: {input_md} not found")
        return 2

    output = Path(args.output).resolve() if args.output else input_md.with_suffix(".pdf")

    if not shutil.which("pandoc"):
        log("error: pandoc not found on PATH. Install with: brew install pandoc")
        return 2

    for engine in ("xelatex", "lualatex", "pdflatex"):
        if shutil.which(engine):
            if try_pandoc(input_md, output, engine):
                print(str(output))
                return 0

    if try_pandoc_wkhtml(input_md, output):
        print(str(output))
        return 0

    html_out = output.with_suffix(".html")
    if render_html_fallback(input_md, html_out):
        log(
            "warning: no PDF engine available — produced HTML instead.\n"
            "  Install one of:\n"
            "    brew install --cask basictex   # then: tlmgr install collection-fontsrecommended\n"
            "    brew install wkhtmltopdf"
        )
        print(str(html_out))
        return 0

    log("error: all rendering backends failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
