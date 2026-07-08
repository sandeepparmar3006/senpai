# SenpAI UI Upgrade — Status

Plan from 2026-07-08 session. Six ranked upgrades; implement one block at a time, verify in browser after each.

## Done

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

## To Do (original ranked order: 2 → 1 → 4 → 3 → 5 → 6)

### Block 4 — Suggestion chips → topical cards (NEXT)
3 small cards instead of identical pills; each with category label matching routes (Lore / Classification / Terminology), optional genre tint or tiny thumbnail. Shows system range → converts visitor to query.

### Block 3 — Streaming polish
- Replace 3-dot typing indicator with skeleton lines matching answer shape.
- Fade streamed tokens in per-chunk (opacity transition on appended spans) instead of raw textContent swap.

### Block 5 — Texture, restrained
- Faint asanoha/seigaiha SVG pattern ~3% opacity behind empty state only.
- Barely-there warm radial gradient at top. No mesh gradients, no purple.

### Block 6 — Route transparency polish
- Mostly done via Block 2 (metrics on cards). Remaining: mono numeral styling for %, review whether expander copy needs work.

### Skipped deliberately (do not add)
Theme toggle, message history/persistence, markdown rendering, scroll choreography.

## Notes
- Local verify: `preview_start` name `senpai` (global launch.json, `vercel dev` port 3000). Project-level `.claude/launch.json` (`senpai-static`, python http.server 4173) exists but preview tool uses the global one.
- Deploy is manual: `vercel --prod` (no git integration — same as manga-reader workflow).
- Changes NOT yet committed or deployed as of session end.
