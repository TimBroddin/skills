# skills

Personal collection of agent skills, installable via the [skills](https://skills.sh) CLI.

## Install

### Option A — Claude Code plugin (Claude Code only)

Installs the whole repo as a single plugin. All skills get registered at once.

```
/plugin install TimBroddin/skills
```

The plugin manifest lives at [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) — adding a new skill is one line there.

### Option B — `skills` CLI (any agent: Claude Code, Codex, Cursor, OpenCode, …)

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
npx skills add TimBroddin/skills --skill research-yt
```

## Skills

### [research-yt](skills/research-yt/)

Deep LLM-driven research over one or more YouTube channels' videos. Lists each channel's catalog, filters videos by topic relevance, transcribes only the relevant ones, then synthesizes a single cross-channel research document with timestamped citations.

Subtitles-first via `yt-dlp`, with optional Whisper fallback. Workspace at `./.research-yt/`; final artifact in cwd.

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
