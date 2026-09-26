# SenpAI

[![CI](https://github.com/sandeepparmar3006/senpai/actions/workflows/ci.yml/badge.svg)](https://github.com/sandeepparmar3006/senpai/actions/workflows/ci.yml)
[![Eval regression gate](https://github.com/sandeepparmar3006/senpai/actions/workflows/eval-gate.yml/badge.svg)](https://github.com/sandeepparmar3006/senpai/actions/workflows/eval-gate.yml)
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

- `semantic_search` — embed the query, Qdrant cosine similarity search over AniList synopses (plot/character/terminology questions)
- `filter_lookup` — Qdrant payload-filtered query over all rows (genre/episode/format filters, lists, counts)
- `opinion_search` — same Qdrant search, filtered to MAL fan reviews instead of synopses (opinion/reception/recommendation questions)

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
| Post-Qdrant-migration verification (2026-07-24) | 100% | 86% | 100% |
| Corpus doubled to 8000 entries + franchise-scoped dedup fix (2026-07-26) | 100% | 91% | 95% |
| Hybrid dense+sparse retrieval, RRF fusion (2026-08-14) | 100% | 95% | 95% |

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
| Post-Qdrant-migration verification (2026-07-24) | 100% (45/45) | 100% (45/45) | 98% (44/45) |
| Corpus doubled to 8000 entries + franchise-scoped dedup fix (2026-07-26) | 100% (45/45) | 98% (44/45) | 96% (43/45) |
| Hybrid dense+sparse retrieval, RRF fusion (2026-08-14) | 100% (45/45) | 98% (44/45) | 96% (43/45) |
| Grown to 69 questions (+`opinion_search`, +multi-constraint filters, +comparative; 2026-08-14) | 100% (69/69) | 97% (67/69) | 97% (67/69) |

The last row isn't a corpus or retrieval change — it's the same corpus and code as the row above, just 24 more questions covering ground the set never tested before. The two new misses are a pre-existing sibling-title ambiguity (JoJo's Bizarre Adventure) and a genuine new finding: see "Eval hardening" below.

The 100% route match on unseen phrasing is the evidence the earlier disambiguation fix generalizes. The first run also surfaced two new argument-extraction bugs, both in `filter_lookup`: the model passed lowercase genres ("sports") against a case-sensitive jsonb match, and the format description omitted `TV_SHORT` so the model couldn't express it. Fixed the same way as before — `enum` constraints on both params — and verified against the live RPC.

As the corpus grew (README's "expanded database" work above added thousands more chunks), a real bug surfaced: `filter_media` capped results at 20 rows ordered by episode count descending, which silently hid shorter well-known titles (HAIKYU!!, Toradora!, Horimiya, Violet Evergarden) behind long-running shows whenever a genre had more matches than the cap — Slice of Life alone has 646 distinct matching titles in the live DB. Reordered to alphabetical (later superseded by popularity ordering, see below) and added a `total_count` column so the model states accurate whole-corpus counts instead of miscounting a truncated sample (this alone fixed two counting questions outright). Two of the eval's own expected values had also gone stale as the corpus grew (movie count assumed 19, actually 308; >200-episode count assumed 6, actually 11) and were corrected against the live DB.

The broad-genre truncation misses called out in the previous run turned out to be fixable after all — the problem wasn't the 50-row cap itself but *which* 50 rows survived it. `filter_media` ordered alphabetically, so a query like "romance shows with at most 13 episodes" (691 matches in the live DB) only ever returned titles starting with A/B, and Horimiya, Kaguya-sama, and Toradora never made the cut. Reordering by `id` — which preserves the original AniList `POPULARITY_DESC` ingestion order — means truncation now discards the *least* popular matches instead of an alphabetical accident (`7b0a772`).

The same run surfaced a second corpus-growth bug on the semantic route: franchises with many entries (Re:ZERO has S1/S2/S3/OVAs, each with near-identical header chunks) flooded the k=5 window with sibling-season metadata, crowding out the top title's own description and character-lore chunks. The answer to "who rescues Subaru?" was in the corpus verbatim but ranked #6. `semantic_search` now over-fetches k×4 candidates and caps non-primary-title chunks at 2 (`61e19bd`). Together the two fixes moved the holdout from 91% to 98% retrieval; the two remaining misses are one keyword-phrasing mismatch and one run-to-run routing sample, not a reproducible failure mode.

### Corpus Expansion to 8000 + Franchise-Scoped Retrieval Fix (2026-07-26)

Corpus doubled again (4000 → 8000 entries). Two enrichments landed alongside the scale-up: AniList's `staff` query now captures role (Director, Original Creator, etc.) via `edges` instead of a flat name list, and a new `recommendations` field feeds a "Similar: ..." line into the main chunk. `ingest/run_ingest.py` was restructured to embed and load each batch into Qdrant immediately with a progress checkpoint (mirroring `run_ingest_reviews.py`'s already-resumable pattern), so a crash mid-run only costs the in-flight batch instead of the whole corpus's paid embedding work. Reviews were re-ingested to cover the new entries; 28 initially failed on Together AI 400s because some reviews open with unbroken image-URL/HTML markup that tokenizes far denser than prose, blowing the 512-token limit despite being under the char cap — fixed by stripping markup before truncating, then backfilled to zero misses.

The scale-up also caused a real, measurable retrieval regression: more franchise siblings (sequels, OVAs, movies, remakes) per query meant `dedupeSiblingTitles`'s assumption — that the single highest-cosine-similarity chunk (`pool[0]`) is always the canonical "primary" title — broke more often, since a spin-off's narrower chunk text can out-score the canonical entry's broader one. Regression eval dropped 86% → 68-73% retrieval. Fix: break ties on `popularity_rank` instead of trusting raw top-1 similarity. First attempt (pick the most popular chunk within a similarity margin of the top score) overcorrected — franchise clusters score so tightly that the margin swallowed the *entire* candidate pool, once promoting an unrelated show ("Dropkick on My Devil!") as primary for a Demon Slayer query just because it was globally more popular. Final fix restricts the tie-break to titles sharing an 8+ character normalized prefix with the top hit (same franchise only); both eval sets returned to pre-regression baseline (91%/98% retrieval on the 22q/45q sets respectively) despite the corpus doubling and the chunk format changing.

### Hybrid Dense+Sparse Retrieval (2026-08-14)

Pure cosine similarity search has a real ceiling: two documented misses couldn't be tuned away with the existing dense-only setup. "Is The Promised Neverland classified as horror?" lost to more strongly horror-tagged titles (Junji Ito adaptations) that out-scored it on raw embedding similarity, and ONE PIECE's canonical entry sometimes didn't even appear in the top-20 candidate pool for character questions — a retrieval-*depth* problem, not a ranking one.

Fixed by adding a named `sparse` BM25-style vector to the Qdrant collection alongside the existing 1024-dim dense vector, fused with client-side Reciprocal Rank Fusion at query time. Sparse vectors are computed with a shared FNV-1a-hashed tokenizer implemented twice — once in `ingest/qdrant_store.py`, once in `api/qdrantStore.js` — and verified byte-identical across both languages via a real cross-language parity test (`tests/test_sparse.py` / `tests/test_sparse.mjs`), not assumed to match. Fusion happens client-side rather than via Qdrant's built-in fusion query specifically so `similarity` in results stays a real cosine score — the existing `MISS_SIMILARITY_THRESHOLD = 0.83` used for query-miss logging, and the UI's similarity-score pills, both depend on that semantic.

Migrating the live collection required a full rebuild: Qdrant has no supported way to add a new named vector to an existing collection. `ingest/backfill_sparse.py` dumps every point (dense vector + payload) to disk first, recreates the collection with both vector configs, then re-upserts from the dump with sparse vectors computed locally — zero additional embedding-API cost, since the dense vectors come straight from the dump. The live migration hit Qdrant write timeouts twice under sustained bulk-write load (a free-tier cluster throughput limit, not a data-loss risk — the on-disk dump meant nothing was ever actually lost), fixed with retry/backoff on the upsert call before the migration completed cleanly.

Both target misses are fixed and confirmed live: the Promised Neverland query now returns Promised Neverland itself as the top hits with the correct Horror genre tag, and the ONE PIECE query now returns the canonical entry as the top-similarity result.

### Eval Hardening: Groundedness Scoring, Coverage Gaps, and a CI Gate (2026-08-14)

Three separate additions, done together as one pass over the eval harness:

**Killed a recurring fixture-drift bug.** Two holdout questions (movie count, count of entries with >200 episodes) carry hardcoded expected numbers that had already silently gone stale twice as the corpus grew (308→552 movies, 11→13 long-running shows — both caught and manually re-patched before). Rather than patch a third time, `eval/eval.py` now computes these live from Qdrant at eval start (`_resolve_live_counts`) instead of hardcoding — fixtures carry a `"live_count"` marker, not a number. Confirmed the drift was still live and current before shipping: the real count came back 599 movies, not the 552 still in the file pre-fix.

**Closed a real zero-coverage gap.** `opinion_search` — the third router tool, live since 2026-07-09 — had never been eval-tested by either question set. Added 15 questions, each title individually verified against the live `jikan_review` corpus before being written into the fixture file, not guessed. Also added verified multi-constraint filter questions and three comparative two-title questions ("does X have fewer episodes than Y"), which surfaced a genuine, previously-undocumented architectural gap: comparative questions route to `semantic_search`, which embeds one combined query and can retrieve chunks for only one of the two named titles, missing the other entirely. That's a routing-architecture gap, not a retrieval-tuning one — documented as a known limitation below, not chased. True negation questions ("anime that are NOT Romance") were deliberately left out of scope after confirming `filter_lookup`'s schema has no exclusion operator and the router currently either substitutes a wrong single category or silently drops the constraint — no correct ground truth exists to test against without first building negation support.

**Added LLM-judge groundedness scoring.** A fourth eval metric, `groundedness_hit`, checks whether an answer's claims are actually supported by the retrieved context — catching a class of bug keyword matching can't, where a correct-sounding answer came from the model's own pretrained knowledge rather than what was actually retrieved. Calibrated against synthetic grounded/hallucinated cases before trusting it, then a real flagged case was manually verified against the actual retrieved context: a Death Note question was correctly flagged HALLUCINATED because the model answered "Ryuk" — true, and well-known — from general knowledge, while the specific chunk retrieved was about a different character having his own Death Note stolen by Ryuk, not a statement that Ryuk dropped his own note into the human world. First measurement (expect several points of run-to-run judge noise, since there's no fixed temperature/seed on the judge call — confirmed this isn't a real accuracy swing by re-running the identical N=22 fixtures twice and seeing route/retrieval/keyword hold exactly steady while only groundedness moved): regression 86%→77% across two runs, holdout 80%.

**Added a CI regression gate.** `.github/workflows/eval-gate.yml` runs the N=22 regression set on any PR touching `api/`, `eval/`, or `ingest/qdrant_store.py` (path-filtered at the trigger level, so unrelated PRs don't burn API calls) and fails the PR if route/retrieval/keyword drop below 90%/85%/85% — thresholds with margin below the current baseline, but the 2026-08-05 incident where a routing-model swap crashed route match to 64% would still fail this gate. Groundedness is reported in the job log but deliberately not gated on, given the run-to-run noise above. Verified with a real throwaway PR, watched live in GitHub Actions to a green pass, not just assumed working from the YAML.

**Known limitations at the time (both since resolved, see 2026-08-15/16 sections below):**
- Comparative two-title questions could miss one of the two named titles in retrieval — needed a dedicated compare-titles code path, not a retrieval fix.
- `filter_lookup` had no negation/exclusion operator — "anime that are NOT X" either substituted a single wrong category or silently ignored the constraint.

### Comparative Titles + Short-Title Retrieval Fix (2026-08-15/16)

Added a dedicated `compare_titles` route (function-calling tool + `resolveTitleFacts`/`titlesMatch` exact-match resolution) so two-title episode comparisons no longer route through `semantic_search`'s single combined-query embedding. Verified live that a bare short title (e.g. "Bleach") could still rank outside the K*4=20 fused semantic pool used for title lookups even though the exact entry exists in the corpus — `resolveTitleFacts` widened to a dedicated 50-result pool (`TITLE_LOOKUP_POOL`, skipping the franchise-dedup step, which isn't relevant to an exact-title lookup) fixed it; both `api/chat.js` and `eval/eval.py` verified against the holdout set's Bleach fixtures before and after.

### Negation Support for filter_lookup (2026-08-16)

Added `exclude_genre` alongside `genre` — Qdrant's native `must_not` filter condition, no new payload index needed (`metadata.genres` was already indexed). Landed the eval fixtures *before* the fix: two negation questions verified against the live corpus (positive picks and canary/must-be-excluded picks both individually confirmed against `metadata.genres`, and re-checked against `filter_lookup`'s real default `limit=50` truncation so the canaries would actually have appeared had exclusion not worked) were committed while still failing, to prove the gap and the test were both real before touching the fix. `retrieval_hit` gained an `expected_titles_none` check (existing `expected_title`/`expected_titles_any` only assert presence, so a dropped constraint would have false-passed). Both fixtures pass post-fix; holdout retrieval rate held at 96% (68/71 → still two pre-existing, unrelated hallucination-judge misses), comfortably above the CI gate's 85% floor.

## Architecture

```
AniList GraphQL (isAdult: false filtered at fetch time)        AniList GraphQL (Reviews API)
        |                                                              |
   ingest/fetch_anilist.py       -> data/raw_anilist.json    ingest/fetch_anilist_reviews.py -> data/raw_reviews.json
        |                                                              |
   ingest/chunk_and_embed.py     -> data/embedded_bge.json   ingest/chunk_and_embed_reviews.py -> data/embedded_reviews.json
        | (Cloudflare Workers AI embeddings, @cf/baai/bge-m3, 1024-dim, both sources)
   ingest/load_to_qdrant.py      -> Qdrant Cloud collection "media_chunks_bge" (source: "anilist" | "jikan_review")
        |
   api/chat.js (Vercel function)
        | check_rate_limit() RPC (Supabase) -> per-IP (15/min) + global (1000/day) cap, fail-open
        | route(query) -> Together chat completion w/ tools (deepseek-ai/DeepSeek-V4.1-Flash), tool_choice: required
        |   |-- semantic_search  -> embed query -> Qdrant hybrid search (dense cosine + sparse BM25, RRF-fused, source: anilist)
        |   |-- filter_lookup    -> Qdrant payload-filtered query, ordered by popularity_rank
        |   |-- opinion_search   -> embed query -> Qdrant hybrid search (dense + sparse, RRF-fused, source: jikan_review)
        | streamGenerate(question, route_results) -> Together chat completion, stream: true
        |   -> answer piped to the client as SSE tokens as they're generated
   public/ (chat UI: renders tokens live, shows route + retrieval detail per answer)
```

Rate limiting (`check_rate_limit`) and query-miss logging (`query_log`) stay on Supabase — small relational tables, unaffected by the vector-store migration below.

Corpus is SFW: the AniList fetch query hard-filters `isAdult: false`, so adult-tagged entries never enter the pipeline.

Model note (2026-09): Together removed serverless access to every embedding model and to `gpt-oss-20b`, so embeddings moved to Cloudflare Workers AI (`bge-m3`, free tier ~10k neurons/day, rolling ~24h cap) and chat/routing to `DeepSeek-V4.1-Flash`. Re-embedding the corpus (`ingest/migrate_to_bge.py`) is resumable across the daily cap. Chat models must be tested with `tool_choice: required` and multi-tool schemas: several serverless models over-fill optional tool arguments or 500 under forced tool choice.

**Corpus growth loop**: two mechanisms keep the corpus from going stale or drifting from what users actually ask about. Every chat query is logged to a `query_log` table (fire-and-forget, never blocks the response) with its route, top similarity score, and an `is_miss` flag — similarity < 0.83 on the semantic routes (empirically, in-corpus hits cluster 0.84–0.90, genuine gaps 0.80–0.83) or zero `total_count` on the filter route. `ingest/review_misses.py` ranks recurring misses for manual triage, so future ingestion can target titles users actually asked for instead of only popularity pages. Separately, a GitHub Actions cron (`.github/workflows/freshness.yml`, Mondays 06:00 UTC) runs `ingest/run_freshness_check.py`, which fetches AniList sorted by `UPDATED_AT_DESC` — catching both newly-added shows and metadata corrections to existing entries in one pass — and upserts through the same cache-aware pipeline, so unchanged chunks are never re-embedded.

**Production hardening**: this is a public URL calling a paid LLM per request with no auth — a real abuse/wallet-drain surface, not a hypothetical one. `check_rate_limit()` (`supabase/rate_limit.sql`) enforces a per-IP cap (15/min) and a global daily ceiling (1000/day) via an atomic Postgres upsert, shared across all serverless instances (in-memory counters reset on every cold start, so they don't work here). It fails open — a `rate_limits` outage degrades to unlimited rather than breaking chat.

## Vector store migration (2026-07-24)

The corpus previously hit Supabase's 500MB free-tier storage cap (594MB across 41,288 rows) — not row-count bloat, but pgvector's HNSW index storing a full second copy of every 1024-dimension embedding for distance calculation, on top of the table's own copy (319MB, over half the total). Tuning the index (`m` 16→8) barely moved the number, since raw vector storage, not graph connectivity, was the actual cost.

Fixed by migrating the vector store to Qdrant Cloud, a purpose-built vector database without the duplicate-storage overhead — `media_chunks` (all 42,175 rows) moved fully to Qdrant; `rate_limits` and `query_log` stayed on Supabase (small relational tables, unaffected). Verified via the full eval suite before cutover (no regression, several metrics improved — see Eval results above) and live spot-checks of all three routes post-deploy.

## Setup

1. **Qdrant Cloud**: create a free cluster at https://cloud.qdrant.io. The `media_chunks` collection (1024-dim, cosine distance) and its payload indexes are created automatically on first run via `ingest/qdrant_store.py::ensure_collection()` — no manual setup needed beyond the cluster itself. Grab the cluster URL + API key.
2. **Supabase**: create a project, run `supabase/rate_limit.sql` in the SQL editor (rate limiting + query-miss logging only — vectors live in Qdrant). Grab the project URL + service role key (Settings > API).
3. **Together AI** (chat/routing): sign up at https://api.together.xyz, generate an API key.
   **Cloudflare Workers AI** (embeddings): free account, then an API token with Workers AI access.
4. Copy `.env.example` to `.env` (ingestion) and `.env.local` (Vercel), fill in `TOGETHER_API_KEY`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`.
5. `pip install -r requirements.txt`
6. `python ingest/run_ingest.py --pages 40` (40 pages x 50 = 2000 anime + 2000 manga entries)
7. `python ingest/run_ingest_reviews.py` — fetches reviews directly via AniList, embeds, loads as `source: "jikan_review"` (second text source, powers `opinion_search`).
8. `python eval/eval.py` — routes every question through the production router, prints route/retrieval/answer rates.
9. `npm install && vercel dev` locally, `vercel --prod` to deploy.

## Roadmap

- ~~Held-out eval set~~ — done, see "Held-out eval" above.
- ~~Streaming responses + rate limiting~~ — done, see "Architecture" and "Production hardening" above.
- ~~AniList reviews as a second text source for opinion-based questions~~ — done: `opinion_search` tool routes to fan reviews (`match_media_chunks` filtered to `source = 'jikan_review'`), source cards show reviewer score instead of similarity.
- ~~UI Polish (Blocks 1-5)~~ — done: Added streaming token animations, skeleton loaders, suggestion cards, and an immersive empty state with a subtle "quiet otaku" aesthetic.
- ~~Corpus expansion & Index upgrade~~ — done: Expanded the catalog to 4000 entries (2000 anime + 2000 manga) and upgraded pgvector index to HNSW for fast search recall. Corpus is SFW throughout: `isAdult: false` filter never touched.
- ~~Query-miss logging for targeted corpus growth~~ — done: `query_log` table flags likely corpus gaps per request; `ingest/review_misses.py` ranks recurring misses for triage. Next step once real traffic accumulates: ingest confirmed-missing titles from AniList by name.
- ~~Weekly freshness check~~ — done: GitHub Actions cron re-ingests the most recently updated AniList entries every Monday, catching new releases and metadata drift without re-embedding unchanged chunks.
- ~~Vector store migration (Supabase pgvector → Qdrant Cloud)~~ — done: see "Vector store migration" above. Fixes the recurring storage-quota ceiling permanently instead of deferring it past each corpus expansion.
- ~~Hybrid dense+sparse retrieval~~ — done, see "Hybrid Dense+Sparse Retrieval" above. Fixes two previously-undocumented dense-only retrieval misses.
- ~~Eval groundedness/hallucination scoring~~ — done, see "Eval Hardening" above. A fourth metric alongside route/retrieval/keyword, distinguishing correct-and-grounded answers from correct-but-not-actually-supported-by-context ones.
- ~~CI regression gate~~ — done: `.github/workflows/eval-gate.yml` fails PRs touching retrieval/routing code if accuracy regresses, verified live against a real test PR.
- Cross-encoder reranker — not currently planned. The two gaps found during the eval-hardening pass (comparative two-title questions, `filter_lookup` negation) were a routing-architecture problem and a missing filter operator respectively — both since fixed (see 2026-08-15/16 sections above), neither was a ranking problem a reranker would have fixed, and nothing else has surfaced to justify the added latency/cost.
- Ingest confirmed-missing titles from `query_log` misses — still traffic-gated, unchanged since it was first noted; nothing to build until real misses accumulate.

## License

MIT
