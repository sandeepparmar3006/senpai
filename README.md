# SenpAI

[![CI](https://github.com/sandeepparmar3006/senpai/actions/workflows/ci.yml/badge.svg)](https://github.com/sandeepparmar3006/senpai/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

RAG assistant over anime/manga metadata with a **function-calling tool router** — every answer streams token-by-token, is grounded in retrieved sources shown as cards with real cover art, and ships a collapsible panel showing exactly which retrieval path it took and why.

### Core UX Features

- **Quiet-Otaku Identity:** Restrained dark theme (`#221f26`) with Japanese serif typography (`Noto Serif JP`) and vertical text highlights.
- **Topical Suggestion Cards:** Category-specific cards (Lore & Opinion, Classification, Terminology) matching retrieval routes to guide user questions.
- **Polished Streaming:** Pulsing skeleton loaders matching the answer card layout and smooth chunk-by-chunk fade-in transitions.

**Live demo: [senpai-seven.vercel.app](https://senpai-seven.vercel.app)**

| Semantic route | Structured route |
|---|---|
| ![Semantic search answering a plot question, with source cards showing cover art and per-source cosine similarity meters](docs/screenshot-semantic.png?v=2) | ![Structured lookup filtering the whole corpus by episode count, with source cards showing episode counts and format per result](docs/screenshot-filter.png?v=2) |

Each source is a card, not a bare pill: cover art (fetched from AniList), title, and either a similarity meter (semantic route) or episode/format detail (structured route). The **"How this was found" panel**, collapsed by default, expands to the router's actual decision (the embedded search query, or the filter criteria applied) — retrieval mechanics are inspectable, not just asserted in this README.

## Why a router

Top-k similarity search silently fails on whole-corpus questions: "which anime have more than 150 episodes?" needs to scan all 250 rows, not the 5 most similar chunks. So the model picks a tool per question via real function-calling (`tool_choice: required`), not a manual classifier prompt:

- `semantic_search` — embed the query, pgvector `match_media_chunks()` RPC over AniList synopses (plot/character/terminology questions)
- `filter_lookup` — `filter_media()` SQL RPC over all rows (genre/episode/format filters, lists, counts)
- `opinion_search` — same `match_media_chunks()` RPC, filtered to MAL fan reviews instead of synopses (opinion/reception/recommendation questions)

One production detail worth knowing: open-weight models sometimes emit a hallucinated answer in `message.content` *alongside* the real `tool_calls`. The implementation discards `content` and only trusts the executed tool result.

## Eval results

22 hand-labeled questions (8 metadata, 12 plot, 2 structured), run through the **same router as production** by `eval/eval.py`:

| Stage | Route match | Retrieval hit | Answer match |
|---|---|---|---|
| Pre-router (semantic-only baseline) | — | 100% | 100% |
| Router added | 82% | 77% | 77% |
| After router fix (`45c27b5`) | 100% | 100% | 100% |
| Expanded Database (1000 entries) + HNSW | 100% | 86% | 91% |
| Corpus doubled to 4000 entries (2000 anime + 2000 manga) | 100% | 77% | 95% |

Adding the router improved real correctness (structured questions get accurate whole-corpus answers instead of top-5 guesses) but introduced routing error as a new, measurable failure surface. The eval caught two reproducible failure modes:

1. Plot questions occasionally misrouted to `filter_lookup` ("what creatures devour humans in Attack on Titan" was classified as structured and returned nothing).
2. `filter_lookup` sometimes extracted wrong or empty arguments ("which anime are movies" didn't pass `format: "MOVIE"`).

Both traced to the same root cause: tool descriptions didn't state the disambiguation rule (named-title plot question wins even when phrased as "what X") and the `format` param had no enum. Fixed both, re-ran the eval unchanged: 100/100/100. That re-run is a regression check validating those two fixes — the next step is a larger held-out set the fixes weren't tuned against, to test generalization.

### Ongoing Series Episode Overrides

AniList API sets `episodes: null` for ongoing series (e.g. `ONE PIECE` and `Detective Conan`). Because the database filters entries using `(metadata->>'episodes')::int >= min_episodes`, these popular ongoing shows were previously filtered out when users searched for long-running anime (e.g., "anime with more than 100 episodes").

To solve this, we introduced `EPISODE_OVERRIDES` in `ingest/chunk_and_embed.py` to map these known ongoing series to their actual episode counts (e.g., 1100 for One Piece, 1120 for Detective Conan). The local json data and Supabase table were rebuilt to ensure they are returned correctly in structured queries. This fix boosted the overall answer keyword match rate from 86% to 91%.

### Character & Lore Semantic Matching

Traditional anime synopses are generic and often omit key character names (such as "Boa Hancock" or "Franky") and key plot/lore facts (like Luffy eating the Gum-Gum Fruit). Consequently, character-specific semantic queries failed to match the correct anime chunk.

To resolve this:
1. **Extended Lore Extraction**: Updated `ingest/fetch_anilist.py` to fetch deep lore including full character descriptions, character roles (MAIN/SUPPORTING), staff members (directors/creators), synonyms, and release seasons.
2. **Multi-Chunk Semantic Architecture**: To accommodate the massive influx of text while strictly adhering to the embedding model's 512-token limit, we refactored `ingest/chunk_and_embed.py` to break a single anime down into **multiple semantic chunks** (e.g., a main metadata chunk, a cast/staff chunk, and numerous character lore chunks).
3. **Database Constraint Evasion & Deduplication**: To store multiple chunks for the same anime without modifying the live database's unique `(source, source_id)` constraint, secondary chunks use composite IDs (e.g. `21_cast`). `api/chat.js` dynamically remaps these back to the base numeric ID using JSON metadata, ensuring thumbnails load seamlessly and `filter_lookup` results are deduplicated.
4. **Smart Caching Optimization**: Re-designed the caching mechanism in `ingest/chunk_and_embed.py` and `ingest/run_ingest.py` to verify if the chunk text changed before calling the Together AI embedding API. This allows instant cache reuse for unchanged entries and dynamic re-embedding only for entries whose characters or lore details changed.

### Review Ingestion Resilience

Initially, the pipeline fetched fan reviews from MyAnimeList via the unofficial Jikan API. However, Jikan enforces strict IP-level rate blocks that routinely halted ingestion after ~250 items.

To resolve this limitation:
1. **Direct AniList GraphQL**: We migrated `ingest/fetch_anilist_reviews.py` to fetch reviews directly from AniList's official GraphQL API, completely bypassing Jikan.
2. **Generous Rate Limits**: AniList permits ~90 requests/minute. By batching 50 anime IDs per GraphQL query, the script now fetches thousands of reviews in seconds without being blocked.
3. **Limitation**: To keep chunk size and embedding costs manageable, the pipeline currently extracts only the top 3 most helpful reviews per anime. 

Two smaller findings from repeat runs, kept because they're what eval work actually looks like:

- **Routing is sampled**, so route match occasionally drops a question run-to-run (21/22 observed on one re-run). Single-run numbers on N=22 carry real variance.
- **One "failure" was a scoring bug, not a model bug**: the model emitted a U+202F narrow no-break space inside "Pirate King", which broke exact substring matching. `keyword_hit()` now normalizes unicode whitespace, with a regression test in `tests/test_eval.py`.

### Universal Corpus (Manga & Deep Lore)

To make the knowledge base truly comprehensive, we expanded the ingestion pipeline to support cross-media data and deeper lore:
1. **Manga Support**: The ingestion engine (`fetch_anilist.py`) now dynamically loops over both `ANIME` and `MANGA` GraphQL queries, and handles manga-specific properties (like `chapters` instead of `episodes`) cleanly.
2. **Franchise Relations (Watch Orders)**: We pull `relations` edges (Sequels, Prequels, Side Stories) from AniList and embed them. To strictly prevent this dynamically sized list from breaking the 512-token limit, relations are truncated to 300 characters before embedding.
3. **Deep Reviews**: With the robust AniList GraphQL pipeline, we doubled the fan review extraction limit from 3 to 6 per item, dramatically enhancing the depth of opinion-based RAG questions.

### Held-out eval (45 unseen questions)

`eval/qa_pairs_holdout.json`: 45 new questions (28 semantic with fresh phrasing, 17 structured with ground truth computed from the raw corpus) written after the router fix and never used to tune it. `python eval/eval.py eval/qa_pairs_holdout.json`:

| Run | Route match | Retrieval hit | Answer match |
|---|---|---|---|
| First run | 100% (45/45) | 93% (42/45) | 93% (42/45) |
| After tool-schema fix | 98% (44/45) | 98% (44/45) | 96% (43/45) |
| Baseline, corpus at ~4,900 chunks (2026-07-16) | 100% (45/45) | 93% (42/45) | 82% (37/45) |
| After `filter_media` limit/bias fix (`cdc51d6`) | 100% (45/45) | 93% (42/45) | 91% (41/45) |
| After sibling-title dedupe (`61e19bd`) + popularity ordering (`7b0a772`) | 98% (44/45) | 98% (44/45) | 96% (43/45) |
| Corpus doubled to 4000 entries (2000 anime + 2000 manga) | 100% (45/45) | 100% (45/45) | 93% (42/45) |

The 100% route match on unseen phrasing is the evidence the earlier disambiguation fix generalizes. The first run also surfaced two new argument-extraction bugs, both in `filter_lookup`: the model passed lowercase genres ("sports") against a case-sensitive jsonb match, and the format description omitted `TV_SHORT` so the model couldn't express it. Fixed the same way as before — `enum` constraints on both params — and verified against the live RPC.

As the corpus grew (README's "expanded database" work above added thousands more chunks), a real bug surfaced: `filter_media` capped results at 20 rows ordered by episode count descending, which silently hid shorter well-known titles (HAIKYU!!, Toradora!, Horimiya, Violet Evergarden) behind long-running shows whenever a genre had more matches than the cap — Slice of Life alone has 646 distinct matching titles in the live DB. Reordered to alphabetical (later superseded by popularity ordering, see below) and added a `total_count` column so the model states accurate whole-corpus counts instead of miscounting a truncated sample (this alone fixed two counting questions outright). Two of the eval's own expected values had also gone stale as the corpus grew (movie count assumed 19, actually 308; >200-episode count assumed 6, actually 11) and were corrected against the live DB.

The broad-genre truncation misses called out in the previous run turned out to be fixable after all — the problem wasn't the 50-row cap itself but *which* 50 rows survived it. `filter_media` ordered alphabetically, so a query like "romance shows with at most 13 episodes" (691 matches in the live DB) only ever returned titles starting with A/B, and Horimiya, Kaguya-sama, and Toradora never made the cut. Reordering by `id` — which preserves the original AniList `POPULARITY_DESC` ingestion order — means truncation now discards the *least* popular matches instead of an alphabetical accident (`7b0a772`).

The same run surfaced a second corpus-growth bug on the semantic route: franchises with many entries (Re:ZERO has S1/S2/S3/OVAs, each with near-identical header chunks) flooded the k=5 window with sibling-season metadata, crowding out the top title's own description and character-lore chunks. The answer to "who rescues Subaru?" was in the corpus verbatim but ranked #6. `semantic_search` now over-fetches k×4 candidates and caps non-primary-title chunks at 2 (`61e19bd`). Together the two fixes moved the holdout from 91% to 98% retrieval; the two remaining misses are one keyword-phrasing mismatch and one run-to-run routing sample, not a reproducible failure mode.

## Architecture

```
AniList GraphQL (isAdult: false filtered at fetch time)        AniList GraphQL (Reviews API)
        |                                                              |
   ingest/fetch_anilist.py       -> data/raw_anilist.json    ingest/fetch_anilist_reviews.py -> data/raw_reviews.json
        |                                                              |
   ingest/chunk_and_embed.py     -> data/embedded.json    ingest/chunk_and_embed_reviews.py -> data/embedded_reviews.json
        | (Together AI embeddings, intfloat/multilingual-e5-large-instruct, 1024-dim, both sources)
   ingest/load_to_supabase.py    -> Supabase pgvector table (source: "anilist" | "jikan_review")
        |
   api/chat.js (Vercel function)
        | check_rate_limit() RPC -> per-IP (15/min) + global (1000/day) cap, fail-open
        | route(query) -> Together chat completion w/ tools (openai/gpt-oss-20b), tool_choice: required
        |   |-- semantic_search  -> embed query -> match_media_chunks() RPC (source: anilist, cosine similarity)
        |   |-- filter_lookup    -> filter_media() RPC
        |   |-- opinion_search   -> embed query -> match_media_chunks() RPC (source: jikan_review)
        | streamGenerate(question, route_results) -> Together chat completion, stream: true
        |   -> answer piped to the client as SSE tokens as they're generated
   public/ (chat UI: renders tokens live, shows route + retrieval detail per answer)
```

Corpus is SFW: the AniList fetch query hard-filters `isAdult: false`, so adult-tagged entries never enter the pipeline.

Model note: `BAAI/bge-*` embeddings and `meta-llama/Llama-3.3-*-Free` chat models are catalog-listed on Together but require a paid dedicated endpoint — the models above were confirmed serverless-accessible by testing directly against the API.

**Corpus growth loop**: two mechanisms keep the corpus from going stale or drifting from what users actually ask about. Every chat query is logged to a `query_log` table (fire-and-forget, never blocks the response) with its route, top similarity score, and an `is_miss` flag — similarity < 0.83 on the semantic routes (empirically, in-corpus hits cluster 0.84–0.90, genuine gaps 0.80–0.83) or zero `total_count` on the filter route. `ingest/review_misses.py` ranks recurring misses for manual triage, so future ingestion can target titles users actually asked for instead of only popularity pages. Separately, a GitHub Actions cron (`.github/workflows/freshness.yml`, Mondays 06:00 UTC) runs `ingest/run_freshness_check.py`, which fetches AniList sorted by `UPDATED_AT_DESC` — catching both newly-added shows and metadata corrections to existing entries in one pass — and upserts through the same cache-aware pipeline, so unchanged chunks are never re-embedded.

**Production hardening**: this is a public URL calling a paid LLM per request with no auth — a real abuse/wallet-drain surface, not a hypothetical one. `check_rate_limit()` (`supabase/rate_limit.sql`) enforces a per-IP cap (15/min) and a global daily ceiling (1000/day) via an atomic Postgres upsert, shared across all serverless instances (in-memory counters reset on every cold start, so they don't work here). It fails open — a `rate_limits` outage degrades to unlimited rather than breaking chat.

## Setup

1. **Supabase**: create a project, run `supabase/schema.sql` and `supabase/rate_limit.sql` in the SQL editor. Grab the project URL + service role key (Settings > API).
2. **Together AI**: sign up at https://api.together.xyz, generate an API key (free-tier credits).
3. Copy `.env.example` to `.env` (ingestion) and `.env.local` (Vercel), fill in `TOGETHER_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
4. `pip install -r requirements.txt`
5. `python ingest/run_ingest.py --pages 40` (40 pages x 50 = 2000 anime + 2000 manga entries)
6. `python ingest/run_ingest_reviews.py` — fetches reviews directly via AniList, embeds, loads as `source: "jikan_review"` (second text source, powers `opinion_search`).
7. `python eval/eval.py` — routes every question through the production router, prints route/retrieval/answer rates.
8. `npm install && vercel dev` locally, `vercel --prod` to deploy.

## Roadmap

- ~~Held-out eval set~~ — done, see "Held-out eval" above.
- ~~Streaming responses + rate limiting~~ — done, see "Architecture" and "Production hardening" above.
- ~~AniList reviews as a second text source for opinion-based questions~~ — done: `opinion_search` tool routes to fan reviews (`match_media_chunks` filtered to `source = 'jikan_review'`), source cards show reviewer score instead of similarity.
- ~~UI Polish (Blocks 1-5)~~ — done: Added streaming token animations, skeleton loaders, suggestion cards, and an immersive empty state with a subtle "quiet otaku" aesthetic.
- ~~Corpus expansion & Index upgrade~~ — done: Expanded the catalog to 4000 entries (2000 anime + 2000 manga) and upgraded pgvector index to HNSW for fast search recall. Corpus is SFW throughout: `isAdult: false` filter never touched.
- ~~Query-miss logging for targeted corpus growth~~ — done: `query_log` table flags likely corpus gaps per request; `ingest/review_misses.py` ranks recurring misses for triage. Next step once real traffic accumulates: ingest confirmed-missing titles from AniList by name.
- ~~Weekly freshness check~~ — done: GitHub Actions cron re-ingests the most recently updated AniList entries every Monday, catching new releases and metadata drift without re-embedding unchanged chunks.

## License

MIT
