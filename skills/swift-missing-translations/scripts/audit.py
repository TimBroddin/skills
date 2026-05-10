#!/usr/bin/env python3
"""Audit a Swift/SwiftUI project's xcstrings catalog for missing
translations and raw source-language literals in UI code.

Usage:
    audit.py <project-root> [--catalog PATH] [--ui-roots DIR1 DIR2 ...]

Output (stdout):
    JSON with two keys:
      - "coverage":  per-language counts (translated / missing / total).
      - "literals":  list of {file, line, literal, suggested_key,
                     auto_localizes} for each likely-untranslated UI
                     literal. `suggested_key` shows the LocalizedStringKey
                     shape Swift would build (with %@ / %lld placeholders);
                     `auto_localizes` is True when the call site is a
                     SwiftUI API that auto-translates literals (Text,
                     Button, Label, Toggle, Picker, navigationTitle,
                     accessibility*, alert).

The agent reads this JSON, picks the catalog, decides what to translate,
and writes back. The script does NOT translate or modify anything —
it only reports.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# Default UI source roots, relative to the project root. The skill
# probes these in order; missing dirs are silently skipped.
DEFAULT_UI_ROOTS = ["Features", "Views", "App", "Components", "Screens"]

# Lines that are obviously NOT user-visible text. Keep this tight — too
# aggressive and we miss real strings; too loose and we drown in noise.
SKIP_LINE = re.compile(
    r"systemImage:|systemName:|imageNamed:|forResource:|withExtension:|"
    r"videoResource:|emoji:|bundleIdentifier|"
    r'#imageLiteral|#Preview|XCTAssert|"key:"|MIME|JSON|'
    r'\.appendingPathComponent|UserDefaults\(suiteName:'
)

# SwiftUI APIs whose literal arguments auto-localize: just need the
# catalog to know the key and they translate. The audit flags literals
# NOT inside one of these as candidates for Swift-side fixes.
AUTO_LOCALIZE_API = re.compile(
    r"\bText\s*\(|"
    r"\bButton\s*\(\s*\"|"
    r"\bLabel\s*\(\s*\"|"
    r"\bToggle\s*\(\s*\"|"
    r"\bPicker\s*\(\s*\"|"
    r"\.navigationTitle\s*\(\s*\"|"
    r"\.alert\s*\(\s*\"|"
    r"\.accessibilityLabel\s*\(|"
    r"\.accessibilityHint\s*\(|"
    r"\.accessibilityValue\s*\("
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project_root", type=Path)
    ap.add_argument(
        "--catalog",
        type=Path,
        help="Path to the xcstrings file. If omitted, the script picks the "
        "first one it finds via rglob from the project root.",
    )
    ap.add_argument(
        "--ui-roots",
        nargs="*",
        help="UI source roots to scan, relative to project root. Defaults "
        f"to: {' '.join(DEFAULT_UI_ROOTS)}",
    )
    args = ap.parse_args()

    project = args.project_root.resolve()
    if not project.is_dir():
        print(f"error: not a directory: {project}", file=sys.stderr)
        return 2

    catalog_path = args.catalog or _find_catalog(project)
    if catalog_path is None:
        print(
            "error: no .xcstrings file found. Pass --catalog explicitly.",
            file=sys.stderr,
        )
        return 2

    catalog = json.loads(catalog_path.read_text())
    source_lang = catalog.get("sourceLanguage", "en")
    strings: dict[str, dict] = catalog.get("strings", {}) or {}

    target_langs = _detect_target_langs(strings, source_lang)
    coverage = _compute_coverage(strings, target_langs)

    ui_roots = [project / r for r in (args.ui_roots or DEFAULT_UI_ROOTS)]
    ui_roots = [r for r in ui_roots if r.is_dir()]

    literals = list(_scan_literals(ui_roots, strings, project))

    print(json.dumps({
        "catalog": str(catalog_path.relative_to(project)),
        "source_language": source_lang,
        "target_languages": target_langs,
        "total_keys": len(strings),
        "coverage": coverage,
        "literals": literals,
    }, ensure_ascii=False, indent=2))
    return 0


# --- catalog --- #

def _find_catalog(project: Path) -> Path | None:
    """Pick the first Localizable.xcstrings under the project, falling back to
    any *.xcstrings if none is named Localizable."""
    preferred = list(project.rglob("Localizable.xcstrings"))
    if preferred:
        return preferred[0]
    fallback = list(project.rglob("*.xcstrings"))
    return fallback[0] if fallback else None


def _detect_target_langs(strings: dict, source_lang: str) -> list[str]:
    """Union of every locale used in any localizations dict, minus the source."""
    seen: set[str] = set()
    for entry in strings.values():
        for lang in (entry.get("localizations") or {}).keys():
            seen.add(lang)
    seen.discard(source_lang)
    return sorted(seen)


def _compute_coverage(strings: dict, target_langs: list[str]) -> dict:
    total = len(strings)
    out = {}
    for lang in target_langs:
        translated = 0
        needs_review = 0
        missing = 0
        empty = 0
        for entry in strings.values():
            unit = (
                (entry.get("localizations") or {})
                .get(lang, {})
                .get("stringUnit", {})
            )
            state = unit.get("state")
            value = unit.get("value")
            if state == "translated" and value:
                translated += 1
            elif state == "needs_review":
                needs_review += 1
            elif value:
                empty += 1  # has a value but unusual state
            elif state is None:
                missing += 1
            else:
                empty += 1
        out[lang] = {
            "translated": translated,
            "missing": missing,
            "empty": empty,
            "needs_review": needs_review,
            "total": total,
            "percent": round(100.0 * translated / total, 1) if total else 0.0,
        }
    return out


# --- literal scan --- #

# Variable / expression names whose Swift interpolation maps to %lld
# (Int) instead of %@ (any). Conservative: when in doubt, %@.
INT_NAME_PATTERN = re.compile(
    r"\.count\b|"
    r"\b(n|i|index|level|newLevel|streak|currentStreak|table|tableNumber|"
    r"rank|number|seconds|minutes|hours|days|age|amount|"
    r"unlockedCount|mistakeCount|attempts(?:ToShow)?|numberOfQuestions)\b"
)


def _scan_literals(
    ui_roots: list[Path],
    catalog_strings: dict,
    project_root: Path,
):
    """Yield dicts describing each likely-untranslated UI literal."""
    for root in ui_roots:
        for path in sorted(root.rglob("*.swift")):
            text = path.read_text()
            for lineno, raw_line in enumerate(text.splitlines(), 1):
                # Strip trailing line comments so a `//` inside a string
                # doesn't truncate. Naive but enough.
                line = _strip_line_comment(raw_line)
                if not line.strip():
                    continue
                if SKIP_LINE.search(line):
                    continue

                for m in re.finditer(r'"((?:[^"\\\n]|\\.)*)"', line):
                    lit = m.group(1)
                    if not lit or not any(c.isalpha() for c in lit):
                        continue

                    suggested = _to_localizedstringkey_shape(lit)
                    if lit in catalog_strings or suggested in catalog_strings:
                        continue

                    yield {
                        "file": str(path.relative_to(project_root)),
                        "line": lineno,
                        "literal": lit,
                        "suggested_key": suggested,
                        "auto_localizes": bool(AUTO_LOCALIZE_API.search(line)),
                    }


def _strip_line_comment(line: str) -> str:
    """Remove `// …` from `line`, but only when the `//` is outside any
    string literal. Quick parse — handles the common cases without
    pulling in a full lexer."""
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            return line[:i]
        i += 1
    return line


def _to_localizedstringkey_shape(swift_literal: str) -> str:
    """Convert a Swift literal with `\\(expr)` interpolations to the
    catalog-key shape SwiftUI's LocalizedStringKey would produce.
    Heuristic Int detection via INT_NAME_PATTERN; everything else %@."""
    out: list[str] = []
    i = 0
    while i < len(swift_literal):
        if swift_literal[i : i + 2] == "\\(":
            depth = 1
            j = i + 2
            while j < len(swift_literal) and depth > 0:
                if swift_literal[j] == "(":
                    depth += 1
                elif swift_literal[j] == ")":
                    depth -= 1
                j += 1
            expr = swift_literal[i + 2 : j - 1]
            out.append("%lld" if INT_NAME_PATTERN.search(expr) else "%@")
            i = j
        else:
            out.append(swift_literal[i])
            i += 1
    return "".join(out)


if __name__ == "__main__":
    sys.exit(main())
