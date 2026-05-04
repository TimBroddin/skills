---
name: youtube-research
description: Deep LLM-driven research over one or more YouTube channels' videos. Lists each channel's catalog, filters videos by topic relevance, transcribes only the relevant ones, then synthesizes a single cross-channel research document with timestamped citations. Use when the user runs /youtube-research or asks to research, summarize, analyze, compare, or extract topics from a YouTube channel, multiple channels, or a specific YouTube video. Subtitles-first via yt-dlp, falls back to local Whisper (mlx-whisper, whisper.cpp, or openai-whisper) for videos without subs. Uses a hidden workspace at ./.youtube-research/ for intermediate artifacts (channel indexes, transcripts) and writes the final research artifact to the current working directory. Asks the user before deleting the workspace at the end.
---

# youtube-research

LLM-driven research over a YouTube channel. The skill scripts handle deterministic I/O (listing, fetching, transcribing); **the synthesis is done by you (Claude) reading the script outputs**.

## Inputs (collect before starting)

Always ask the user for:
1. **Channel(s)** — one or more handles, URLs, or @names (e.g. `@veritasium`, or `@veritasium, @3blue1brown`). Multiple channels are encouraged when the user wants comparison or broader coverage.
2. **Topic** — research focus (e.g. "quantum computing"). Used as the topic-file slug.
3. **Specific video** *(optional)* — single URL/ID to scope to one video instead of the channel(s).
4. **Output format** — markdown (default) or PDF, asked at runtime.
5. **How many videos per channel to consider** — defaults to newest 50 *per channel*; ask if the user wants more/less. Be explicit that the limit is per-channel so the user can budget their time.
6. **Specific question to answer** *(optional)* — if the user has a concrete question they want the research to answer (e.g. "Which approach is most cost-effective for small teams?"), capture it. When present, this becomes the spine of the synthesis: every section should serve the answer, and the artifact gets a dedicated **Answer** section up top. If absent, fall back to the broader topic survey.

Don't guess any of these. If the user names multiple channels but not the topic (or vice versa), ask before running anything — multi-channel runs cost N× the metadata fetch time.

**Use the `AskUserQuestion` tool to collect these inputs** rather than free-form prose. One call, batched questions (output format, video limit, optional research question), so the user answers everything in one shot. Channels and topic usually arrive in the user's initial message — only ask for what's actually missing. For the optional research question, phrase it so "no specific question, just survey the topic" is one of the choices, so the user isn't forced to invent one.

## Workspace + artifact layout

Two distinct things live in the current working directory:

```
./                                  # cwd — user's working directory
├── <topic-slug>.md                 # ← FINAL ARTIFACT (or .pdf). Goes in cwd.
└── .youtube-research/                   # ← HIDDEN WORKSPACE. Cache + intermediates.
    └── channels/
        └── <slug>/
            ├── channel.json        # input + slug + counts
            ├── index.json          # full video list w/ titles + descriptions (cached)
            └── videos/
                └── <video-id>/
                    ├── meta.json   # title, upload_date, duration, url, source
                    └── transcript.md  # transcript with [HH:MM:SS] anchors
```

Rules:
- The **final artifact** (the user-facing research document) goes in **cwd**, named `<topic-slug>.md` or `<topic-slug>.pdf`. Never bury it inside `.youtube-research/`.
- The **workspace** (`.youtube-research/`) holds everything else: channel listings, transcripts, intermediate data. It's hidden so it doesn't clutter the user's directory listing, and it's a single dotfile so it's easy to `.gitignore` or delete.
- All scripts take `--kb ./.youtube-research` so the workspace path is consistent.
- Slug rule: lowercase, alphanumerics + hyphens. Strip leading `@`.

## Workflow

### Step 1 — Check dependencies (once)

```bash
python3 scripts/check_deps.py
```

Verifies `yt-dlp` and `ffmpeg` and detects which Whisper flavor is available (`mlx-whisper` → `whisper.cpp` → `openai-whisper`). If `yt-dlp` is missing, stop and tell the user (`brew install yt-dlp`). If no Whisper, continue but warn that Whisper-only videos will be skipped.

### Step 2 — List each channel's catalog

Single channel:
```bash
python3 scripts/list_channel.py --channel "@veritasium" --kb ./.youtube-research --limit 50
```

Multiple channels (one call, repeat `--channel` or pass comma-separated):
```bash
python3 scripts/list_channel.py \
  --channel "@veritasium" \
  --channel "@3blue1brown" \
  --kb ./.youtube-research --limit 50

# Equivalent:
python3 scripts/list_channel.py --channel "@veritasium,@3blue1brown" --kb ./.youtube-research --limit 50
```

Output shape:
- **Single channel:** `{channel_slug, channel, count, videos: [...]}` (each video carries its own `channel_slug`).
- **Multiple channels:** `{channels: [{channel_slug, channel, count, videos: [...]}, ...], total_videos}`.

`--limit` applies **per channel**, not in total. The cache is per-channel as well — re-running with a new channel only fetches the new one's metadata.

### Step 3 — LLM filter: pick topic-relevant videos

**You read the JSON from Step 2 and decide which videos look topic-relevant** based on titles + descriptions. Be inclusive at this stage — false negatives are expensive (the video gets dropped from the research), false positives are cheap (you'll catch them when reading the transcript in Step 5).

If the user provided a specific research question (Input 6), bias the filter slightly toward videos that look like they could *answer* it, not just touch the topic. A broad topic match without question-relevance is still a candidate; just rank it lower.

For multi-channel runs, do this filter **per channel** so you don't lose track of which video came from where. Build one ID list per channel slug (you'll need them in Step 4).

If the combined candidate set is large or ambiguous, surface it to the user before transcribing: "I found 8 from @veritasium and 6 from @3blue1brown — want me to drop any?" Get a quick confirm, then proceed.

Output of this step: a comma-separated list of video IDs **per channel slug**.

### Step 4 — Transcribe only the filtered set

`fetch_transcripts.py` takes one channel per invocation. For multi-channel runs, call it once per channel:

```bash
python3 scripts/fetch_transcripts.py \
  --channel "@veritasium" \
  --ids "id1,id2,id3" \
  --kb ./.youtube-research

python3 scripts/fetch_transcripts.py \
  --channel "@3blue1brown" \
  --ids "id4,id5" \
  --kb ./.youtube-research
```

These calls are independent — run them in parallel as separate Bash tool calls in one message to cut wall time roughly in half.

For each ID: skip if `transcript.md` exists, else try manual subs → auto-subs → Whisper. Writes `meta.json` + `transcript.md` per video under `<kb>/channels/<slug>/videos/<id>/`. Streams progress to stderr; emits a JSON summary on stdout.

Other modes:
```bash
# Single video (auto-detects channel from metadata)
python3 scripts/fetch_transcripts.py --video "https://www.youtube.com/watch?v=XXXX" --kb ./.youtube-research

# Force Whisper even when subs exist (rare — usually for non-English or low-quality auto-subs)
python3 scripts/fetch_transcripts.py --channel "@veritasium" --ids "id1" --kb ./.youtube-research --force-whisper
```

### Step 5 — Synthesize the artifact

**You (Claude) do this directly** — read each transcript under `./.youtube-research/channels/<slug>/videos/*/transcript.md`, extract topic-relevant passages with their `[HH:MM:SS]` anchors, and write **the final artifact to `./<topic-slug>.md` in the current working directory** (NOT inside `.youtube-research/`). This is the user-facing deliverable; it lives at the top level of cwd so the user can find it immediately.

**Critical: read before citing.** Title + description filtering in Step 3 has false positives. A video titled "The laziest way to get users" might turn out to be about Airtable plugin marketplaces, not App Store growth. **Drop false positives in this step** — never cite from a transcript you haven't actually read for topic relevance. It's better to publish a topic file built on 1 strong source than 5 padded ones.

**Be honest about coverage, per channel.** If a channel only tangentially covers the topic, say so in the "Filter notes" section (e.g. "1 of 50 @veritasium videos was directly on-topic; that channel isn't a deep ASO library"). This is more useful to the user than fabricating breadth.

**If the user provided a specific question (Input 6), answer it first.** Add an **Answer** section right after the H1, before TL;DR. Give the most defensible answer the transcripts support, with the strongest 2-4 citations. If the transcripts don't actually answer the question, say that explicitly — "the surveyed videos don't directly answer this; the closest relevant material is X" — rather than padding.

**Multi-channel synthesis adds two responsibilities:**
1. **Attribute every claim to its source channel.** Citations always include the channel name so the reader can weigh credibility per source.
2. **Surface agreement and disagreement across channels.** This is the main reason a user asks for multi-channel research — the "Cross-channel comparison" section is where you do that work. Don't just concatenate per-channel summaries.

Required structure (single channel — keep the channel name out of the H1 if it's confusing, otherwise include it):

```markdown
# <Topic> — <Channel>

*Generated <date> from N relevant videos (out of M scanned)*

## Answer
*(only if the user provided a specific question — otherwise omit this section)*
**Question:** <user's question, verbatim>

<Direct answer in 2-5 sentences, grounded in the transcripts. If the transcripts don't actually answer it, say so.>

Supporting citations:
- [<video title> @ HH:MM:SS](<video-url>&t=<seconds>s) — <one-line why this supports the answer>

## TL;DR
2-4 sentences capturing the channel's overall position on the topic.

## Key claims
- **<claim>** — [<video title> @ HH:MM:SS](<video-url>&t=<seconds>s)
  Brief context (1-2 sentences from surrounding transcript).
- ...

## Recurring themes
- ...

## Tensions / evolution over time
(If only one video covers the topic, say so explicitly rather than inventing tensions.)

## Source videos
- [<title>](<url>) — <upload_date> — <one-line summary> — *Source: <subs|auto-subs|whisper>*
- ...

## Filter notes
- N of M scanned videos turned out to be directly on-topic.
- Dropped after reading: <video title> (reason — e.g. "actually about X, not the topic").
- Coverage assessment: <one line on whether this channel is a strong/weak source for the topic>.

---
*This skill just saved you from having to watch <H>h <M>m of videos.*
```

Required structure (multiple channels):

```markdown
# <Topic> — <Channel A> + <Channel B> [+ ...]

*Generated <date> from N relevant videos (out of M scanned across K channels)*

## Answer
*(only if the user provided a specific question — otherwise omit this section)*
**Question:** <user's question, verbatim>

<Direct answer in 2-5 sentences. Note any cross-channel agreement or disagreement on the answer itself.>

Supporting citations:
- [<video title> @ HH:MM:SS](<video-url>&t=<seconds>s) *(<channel name>)* — <one-line why this supports the answer>

## TL;DR
2-4 sentences capturing the consensus and the most interesting disagreement.

## Cross-channel comparison
A short table or list of where the channels agree vs. differ on the topic. This is the headline value of a multi-channel run; don't skip or under-invest here.

| Question / sub-topic | <Channel A> | <Channel B> |
|---|---|---|
| ... | ... | ... |

## Key claims
Group by sub-topic (not by channel) so cross-channel agreement/disagreement is visible inline. Each citation MUST name the channel:
- **<claim>** — [<video title> @ HH:MM:SS](<video-url>&t=<seconds>s) *(<channel name>)*
  Brief context.

## Recurring themes
What shows up across multiple channels — and where each channel adds its own twist.

## Tensions / disagreements
Where the channels disagree, hedge differently, or emphasise different parts of the topic.

## Source videos
Grouped by channel:

### <Channel A>
- [<title>](<url>) — <upload_date> — <one-line summary> — *Source: <subs|auto-subs|whisper>*

### <Channel B>
- ...

## Filter notes
- Per-channel coverage: <Channel A>: N of M relevant; <Channel B>: P of Q relevant.
- Dropped after reading: <video> (<channel>, reason).
- Coverage assessment per channel: which is a strong vs. weak source for this topic.

---
*This skill just saved you from having to watch <H>h <M>m of videos across <K> channels.*
```

Build the timestamp deep-link as `<url>&t=<seconds>s` where seconds = HH*3600 + MM*60 + SS. Always include the source field (subs/auto-subs/whisper) per video — auto-subs and Whisper can have transcription errors that affect quote accuracy; flag any quote that looks garbled.

**Watch-time footer.** Sum the `duration` field (seconds) from each cited video's `meta.json` — only the videos that survived Step 5's read-and-drop, since those are the ones the user would otherwise have had to watch. Format as `<H>h <M>m` (drop the hours segment if zero). Append the italicized footer line to the artifact exactly as shown in the template. For multi-channel runs, also report how many channels contributed.

### Step 6 — Render output

If the user picked **markdown**, you're done — point them at `./<topic-slug>.md` in cwd.

If the user picked **PDF**, run:

```bash
python3 scripts/render_pdf.py ./<topic-slug>.md
```

The script tries multiple backends in order: `pandoc + xelatex` (best, full unicode), then `lualatex`, then `pdflatex`, then `pandoc + wkhtmltopdf`, then standalone HTML as a last resort. Prints the output path on stdout. The PDF lands next to the markdown in cwd.

If the script fails with "no PDF engine available," it produces HTML instead and tells the user how to install a PDF engine (`brew install --cask basictex` or `brew install wkhtmltopdf`).

### Step 7 — Ask before clearing the workspace

After the artifact is in cwd, ask the user whether to delete `./.youtube-research/`. Phrase the tradeoff plainly so they can make an informed call:

> The research artifact is at `./<topic-slug>.md`. The workspace `./.youtube-research/` (~<size>, transcripts + channel indexes) is no longer needed for this artifact, but keeping it makes future runs on these same channels much faster (no re-fetching metadata or re-transcribing). Delete it, or keep it for next time?

**Default: keep.** The cache is the most expensive thing to recreate — channel listings cost ~1s per video and transcripts cost real wall-clock time (especially Whisper). Only delete on explicit user confirmation.

If the user says delete:
```bash
rm -rf ./.youtube-research
```

Don't delete without asking — even in auto mode, `rm -rf` on a directory the user might want is the kind of action that warrants confirmation. If the user has previously told you "always clean up after yourself" or similar, you can skip the prompt; otherwise ask.

## Notes

- Always work in the user's **current working directory** — never write workspace files inside the skill folder. The workspace is `./.youtube-research/`; the final artifact is `./<topic-slug>.md` (or `.pdf`).
- Transcripts and the channel index are cached per channel inside `.youtube-research/`. Re-running on the same channel(s) only fetches new videos and new metadata; adding a new channel to a multi-channel run won't re-fetch the others. Deleting `.youtube-research/` discards this cache.
- Be transparent in the topic file about transcription source per video (manual subs vs auto-subs vs Whisper) — Whisper output can have errors that affect synthesis quality.
- For very large channels (1000+ videos), bump `--limit` deliberately and warn the user about the per-video metadata cost (~1s each). With multiple channels, the cost multiplies — N channels × `--limit` seconds for the worst case (cold cache).
- **Parallelism:** when transcribing multiple channels, fire the per-channel `fetch_transcripts.py` calls in parallel as separate Bash tool calls in one message. The listing step processes channels sequentially inside one script run, which is fine for newest-50 but worth knowing for larger limits.
