# skills

Personal collection of agent skills, installable via the [skills](https://skills.sh) CLI.

## Install

### Option A — `skills` CLI (recommended; works with any agent)

Cross-agent install via [skills.sh](https://skills.sh) — Claude Code, Codex, Cursor, OpenCode, and more.

Install all skills globally:

```bash
npx skills add TimBroddin/skills --all
```

List available skills:

```bash
npx skills add TimBroddin/skills --list
```

Install one skill:

```bash
npx skills add TimBroddin/skills --skill youtube-research
```

### Option B — Claude Code plugin (Claude Code only)

Installs the whole repo as a single plugin. All skills get registered at once.

```
/plugin install TimBroddin/skills
```

The plugin manifest lives at [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) — adding a new skill is one line there.

## Skills

### [app-store-aso](skills/app-store-aso/)

Generate optimized Apple App Store metadata with ASO best practices, character-limit validation, competitive analysis, and screenshot strategy. Activates on App Store optimization, metadata review, or screenshot questions.

Pairs well with [astro-mcp-server](https://github.com/TimBroddin/astro-mcp-server) (full ASO MCP) and [krankie](https://github.com/timbroddin/krankie) (lightweight CLI for keyword rank tracking).

Includes a `validate_metadata.py` script that checks Apple's character limits independently of the agent.

### [swift-missing-translations](skills/swift-missing-translations/)

Audit a Swift/SwiftUI project's `Localizable.xcstrings` (and `AppShortcuts.xcstrings`) for missing translations, compute per-language coverage, find raw source-language literals still hard-coded in UI code, and bulk-translate the gaps.

Reads the catalog's `sourceLanguage` and target locales from the project — works regardless of source language. Covers the trickier patterns the catalog alone can't fix: `LocalizedStringKey` vs `String`-typed surfaces, `{name}`-placeholder keys imported from JS i18n libraries, the `Hashable`-on-`LocalizedStringResource` compile error, the `String(localized:)` vs in-app-language-picker pitfall (with a `LocaleBundle` helper), and the `AppShortcuts.xcstrings` + `AppEnum`-for-Int-in-phrases setup for App Intents.

### [youtube-research](skills/youtube-research/)

Deep LLM-driven research over one or more YouTube channels' videos. Lists each channel's catalog, filters videos by topic relevance, transcribes only the relevant ones, then synthesizes a single cross-channel research document with timestamped citations.

Subtitles-first via `yt-dlp`, with optional Whisper fallback. Workspace at `./.youtube-research/`; final artifact in cwd.

Requires: `yt-dlp`, `ffmpeg` (`brew install yt-dlp ffmpeg`). Whisper optional.

## Layout

```
skills/
└── <skill-name>/
    ├── SKILL.md          # frontmatter + instructions for the agent
    └── scripts/          # any helper scripts the skill calls
```

Each skill is self-contained — its `SKILL.md` declares its `name` and `description`, and any scripts live alongside it.

## License

MIT
