# CLAUDE.md — SenpAI Project Reference

This guide provides commands, code style rules, and structural findings for developers (and AI assistants like Claude) working on the SenpAI project.

---

## 🛠 Commands

### Development & Execution
* **Run web client & API (Local Dev Server)**: `npm install && vercel dev`
* **Deploy to Production**: `vercel --prod`
* **Run Data Ingest (Python)**: `./.venv/bin/python ingest/run_ingest.py --pages 5` (requires `.env` file setups)

### Tests & Evaluation
* **Run offline unit tests**: `./.venv/bin/python tests/test_eval.py`
* **Run full evaluation pipeline (Regression)**: `./.venv/bin/python eval/eval.py`
* **Run full evaluation pipeline (Holdout)**: `./.venv/bin/python eval/eval.py eval/qa_pairs_holdout.json`

---

## 🏗 Codebase & Routing Architecture

SenpAI is a RAG assistant that retrieves anime/manga metadata. It implements a function-calling tool router (`openai/gpt-oss-20b`) supporting:
1. `semantic_search`: Cosine similarity search (Qdrant) for plot/synopsis/character/terminology-based questions. Searches `media_chunks` collection where `source = 'anilist'`.
2. `filter_lookup`: Qdrant payload-filtered query for whole-corpus filters (genre, format, episode counts), ordered by `metadata.popularity_rank`.
3. `opinion_search`: Cosine similarity search over MAL/Jikan fan reviews for opinion/reception/recommendation questions. Same Qdrant collection, `source = 'jikan_review'`.

Second text source (reviews) is ingested separately from the AniList pipeline: `ingest/fetch_anilist_reviews.py` -> `ingest/chunk_and_embed_reviews.py` -> `ingest/load_to_qdrant.py` (via `ingest/run_ingest_reviews.py`), querying reviews from AniList's GraphQL API.

* **Backend**: Vercel Node.js Serverless function at [api/chat.js](file:///Users/sandeepparmar/.claude/projects/senpai/api/chat.js), using [api/qdrantStore.js](file:///Users/sandeepparmar/.claude/projects/senpai/api/qdrantStore.js) for all vector search/filter calls.
* **Frontend**: Single page pure HTML/JS/CSS app served out of [public/](file:///Users/sandeepparmar/.claude/projects/senpai/public/).
* **Database**: Qdrant Cloud for vector search (`media_chunks` collection). Supabase PostgreSQL for rate limiting (`rate_limits`) and query-miss logging (`query_log`) only -- no vector data lives there anymore (migrated 2026-07-24, see README.md "Vector store migration").

---

## ⚠️ Important Findings & Gotchas

* **Vector store migrated Supabase pgvector → Qdrant Cloud (2026-07-24), resolves the storage-quota ceiling**: `media_chunks` used to account for the entire Supabase DB size (594MB/500MB free-tier quota, 119% at last measurement) — not bloat, but pgvector's HNSW index (319MB, ~54% of total) storing a full duplicate copy of every 1024-dim vector for distance calc, on top of the table's own copy. Tuning the index (`m` 16→8) barely helped (320MB→319MB), confirming raw vector storage was the cost driver, not graph structure. Fixed by moving `media_chunks` fully to Qdrant (`ingest/qdrant_store.py` Python, `api/qdrantStore.js` JS — both used by ingest scripts, `api/chat.js`, and `eval/eval.py`); `rate_limits`/`query_log` stayed on Supabase. `media_chunks` table + `match_media_chunks`/`filter_media` RPCs + HNSW index have been dropped from Supabase entirely (verified live, confirmed gone).
* **Qdrant collection self-provisions, no manual setup needed**: `ensure_collection()` in `ingest/qdrant_store.py` creates the `media_chunks` collection (1024-dim, cosine distance) and its 6 payload indexes (`source`, `metadata.genres`, `metadata.format`, `metadata.episodes`, `metadata.anilist_id`, `metadata.popularity_rank`) on first run if they don't exist — no SQL-editor-paste step like the old Postgres RPCs needed.
* **`popularity_rank` replaces Postgres's `id`-ordering for `filter_lookup`**: Qdrant's point IDs are deterministic UUIDs (`uuid5` of `source:source_id`) with no inherent order, unlike Postgres's bigserial `id`. `metadata.popularity_rank` is assigned per-chunk in `ingest/chunk_and_embed.py` (entry position × 1000, + a per-chunk-type offset so an entry's own main/cast/lore chunks always cluster together). Critically, `ingest/load_to_qdrant.py`'s `load()` preserves a point's *existing* `popularity_rank` on re-ingest rather than overwriting it — `run_freshness_check.py` fetches by recency (`UPDATED_AT_DESC`), not popularity, so blindly reassigning rank from that fetch order would corrupt ranking for exactly the most-actively-updated titles every weekly run.
* **Rate Limits**:
  * Enforced in the database layer via atomic updates (`check_rate_limit` RPC).
  * Limits are set to 15 queries/minute per IP, and 1000 queries/day globally.
  * Node.js fails open on database limiter exceptions to preserve service uptime.
  * **AniList API Rate Limit (reviews ingestion)**: AniList public API has a rate limit of ~90 requests/minute. The fetch script uses batching and a 0.7s fetch delay (`REQUEST_DELAY` in `fetch_anilist_reviews.py`) to avoid 429s.
* **Corpus Expansion Guidelines**:
  * To increase SenpAI's domain knowledge, continue expanding the anime corpus. Ingest more pages of popular anime (e.g. increase page count using `python ingest/run_ingest.py --pages 20` or higher to cover more anime series) and fetch corresponding AniList reviews using `python ingest/run_ingest_reviews.py`.
* **Vector Embeddings**:
  * Generated using `intfloat/multilingual-e5-large-instruct` (1024-dim).
  * Qdrant collection configured with `Distance.COSINE` — the returned `score` is already the cosine similarity directly (no `1 - distance` transform needed, unlike the old pgvector `<=>` operator). `MISS_SIMILARITY_THRESHOLD = 0.83` in `api/chat.js`/`eval.py` carried over unchanged post-migration; verified against the eval baseline before cutover, not just assumed to transfer.

---

## 🎨 Code Style & Design Guidelines

* **Frontend CSS**: Use Vanilla CSS variables, responsive design, and standard flex/grid layouts. Avoid TailwindCSS unless explicitly requested.
* **Typography & Vibe**: "Quiet-otaku" theme: restrained accents (`#d6603f`), dark surfaces (`#221f26`), and Japanese typography details (Noto Serif JP/Sans JP).
* **Accessibility**: Maintain a WCAG AA baseline: explicit focus visible outlines, minimum 44px tap targets, and respect `prefers-reduced-motion` settings.
