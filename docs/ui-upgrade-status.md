# SenpAI UI Upgrade — Status

Plan from 2026-07-08 session. Six ranked upgrades; implement one block at a time, verify in browser after each.

**ACTIVE** — Blocks 1, 2, 3, & 4 shipped and verified. Blocks 5 & 6 not started. Start at Block 5 next.

## Done

### Block 3 — Streaming polish (verified)
- Replaced 3-dot typing indicator with skeleton lines (`.skeleton-lines` containing 3 pulsing `.skeleton-line` components) matching answer shape.
- Added smooth per-chunk token fade-in animations using `.token-chunk` spans inserted before the trailing cursor.
- Files: `public/app.js`, `public/style.css`.

### Block 4 — Suggestion chips → topical cards (verified)
- Replaced suggestion chips with a `.suggestions-grid` containing 3 `.suggestion-card` buttons.
- Cards categorized by route range: Lore & Opinion, Classification, and Terminology.
- Designed with serif queries, tags, descriptions, hover transitions, and active click scaling.
- Files: `public/index.html`, `public/style.css`, `public/app.js`.

### Block 2 — Answer cards + source cards (verified live)
- Assistant replies: full-width bordered card (`.bubble-row.assistant .bubble`), user messages stay bubbles.
- Source pills replaced with `.source-card` grid: AniList cover thumbnail + title + similarity meter (`.meter`) with % label. Filter-lookup route shows `eps · format` instead.
- Covers fetched client-side from AniList GraphQL by `source_id` (batch `id_in`, module-level `coverCache` Map, silent degrade if fetch fails — cards render without thumbs).
- "How this was found" expander trimmed to method sentence only (per-source metrics now on cards). `.how-list`/`.how-score` CSS removed.
- Files: `public/app.js` (fetchCovers, appendSources rewrite, callers pass `route`), `public/style.css`.

### Block 1 — Empty-state identity (verified)
- Big 狐 mark (`.empty-mark`, 88px, accent radial glow via `::before`).
- Noto Serif JP for empty heading + mark + tategaki; loaded via Google Fonts preconnect + `display=swap` in `index.html`; `--font-serif` token added.
- Vertical accent 先輩に聞け (`.tategaki`, writing-mode vertical-rl, hidden < 480px).
- Files: `public/index.html`, `public/style.css`.

## New item added 2026-07-09

### Corpus expansion
Corpus too small: 250 entries (5 AniList pages, popularity-sorted) means many titles users ask about aren't in the database. Increase page count / add more sources to grow corpus size for experimentation. SFW-only stays as-is (`isAdult: false` filter, README/positioning unchanged) — not up for reversal.

## To Do (original ranked order: 2 → 1 → 4 → 3 → 5 → 6)

### Block 5 — Texture, restrained
- Faint asanoha/seigaiha SVG pattern ~3% opacity behind empty state only.
- Barely-there warm radial gradient at top. No mesh gradients, no purple.

### Block 6 — Route transparency polish
- Mostly done via Block 2 (metrics on cards). Remaining: mono numeral styling for %, review whether expander copy needs work.

### Skipped deliberately (do not add)
Theme toggle, message history/persistence, markdown rendering, scroll choreography.

## Out-of-plan work done 2026-07-09 (separate from the 6 UI blocks above)

- **`opinion_search` third route** — Jikan/MAL fan reviews as a second RAG text source, per README roadmap item. 747 reviews fetched/embedded/loaded (`source: "jikan_review"`). Router prompt sharpened after live testing showed "is X worth watching" initially misrouting to `semantic_search`; verified fixed (opinion → `opinion_search`, plot → `semantic_search`, no regression). New files: `ingest/fetch_jikan_reviews.py`, `ingest/chunk_and_embed_reviews.py`, `ingest/run_ingest_reviews.py`. Committed `cc3db47`, deployed, smoke-tested live.
- **Production bugfix**: `supabase/filter_media.sql` was a stale duplicate of the `filter_media` RPC (different signature, no `source_id`) that had been applied to the live DB alongside `schema.sql`'s version — two overloads of the same function name caused every `filter_lookup` call to fail (`PGRST203`), and before that, the missing `source_id` silently broke cover-art lookup on filter-route cards. Deleted the stale file, you dropped the duplicate DB function directly. Verified: filter route works, cover art loads. Committed `6cb8571`, pushed.
- **Screenshots retaken** for both semantic and filter routes against live prod, confirmed real loaded cover art (not blank placeholders) before finalizing README.
- `embed_text()` in `ingest/chunk_and_embed.py` now retries on transient network errors (up to 6 attempts, capped backoff) — hit a real Together API 503 mid-batch during this session's review ingestion; `chunk_and_embed_reviews.py` also skips (not crashes on) a single review that fails after retries.

## Notes
- Local verify: `preview_start` name `senpai` (global launch.json, `vercel dev` port 3000). Project-level `.claude/launch.json` (`senpai-static`, python http.server 4173) exists but preview tool uses the global one.
- Deploy is manual: `vercel --prod` (no git integration — same as manga-reader workflow).
- Changes NOT yet committed or deployed as of session end.
