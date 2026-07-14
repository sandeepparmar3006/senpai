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
1. `semantic_search`: Cosine similarity vectors search for plot/synopsis/character/terminology-based questions. Searches `media_chunks` where `source = 'anilist'`.
2. `filter_lookup`: Structured SQL metadata filtering query for whole-corpus filters (genre, format, episode counts).
3. `opinion_search`: Cosine similarity search over MAL/Jikan fan reviews for opinion/reception/recommendation questions. Searches `media_chunks` where `source = 'jikan_review'` via the same `match_media_chunks` RPC with `source_filter` set.

Second text source (reviews) is ingested separately from the AniList pipeline: `ingest/fetch_anilist_reviews.py` -> `ingest/chunk_and_embed_reviews.py` -> `ingest/load_to_supabase.py` (via `ingest/run_ingest_reviews.py`), querying reviews from AniList's GraphQL API.

* **Backend**: Vercel Node.js Serverless function at [api/chat.js](file:///Users/sandeepparmar/.claude/projects/senpai/api/chat.js).
* **Frontend**: Single page pure HTML/JS/CSS app served out of [public/](file:///Users/sandeepparmar/.claude/projects/senpai/public/).
* **Database**: Supabase PostgreSQL with `pgvector` and rate limiting.

---

## ⚠️ Important Findings & Gotchas

* **`match_media_chunks` needs manual redeploy on schema change**: no direct Postgres connection string in `.env` (only the REST service-role key), so `supabase/schema.sql` changes to this RPC (e.g. the `source_filter` param added for `opinion_search`) must be pasted into the Supabase SQL editor by hand — there's no CLI/migration path from this repo currently.
* **RPC Signature Mismatch (resolved 2026-07-09, watch for regressions)**:
  * There used to be two conflicting `filter_media` definitions in this repo: `supabase/schema.sql` (`returns (source_id text, title text, metadata jsonb)`) and a stale `supabase/filter_media.sql` (`returns (title text, metadata jsonb)`, no `source_id`, extra `limit_count` param). The stale file was deleted, but the *live* production DB had it applied at some point, which silently broke cover-art lookup on `filter_lookup` results (`api/chat.js` reads `r.source_id`, got `undefined`, client never fetched AniList cover art for those cards).
  * `schema.sql`'s version is the only correct one now. If filter-route source cards ever show blank thumbnails again, re-run `filter_media` from `schema.sql` in the Supabase SQL editor — the DB function silently diverging from the repo is the likely cause, not a frontend bug.
* **Rate Limits**:
  * Enforced in the database layer via atomic updates (`check_rate_limit` RPC).
  * Limits are set to 15 queries/minute per IP, and 1000 queries/day globally.
  * Node.js fails open on database limiter exceptions to preserve service uptime.
  * **AniList API Rate Limit (reviews ingestion)**: AniList public API has a rate limit of ~90 requests/minute. The fetch script uses batching and a 0.7s fetch delay (`REQUEST_DELAY` in `fetch_anilist_reviews.py`) to avoid 429s.
* **Corpus Expansion Guidelines**:
  * To increase SenpAI's domain knowledge, continue expanding the anime corpus. Ingest more pages of popular anime (e.g. increase page count using `python ingest/run_ingest.py --pages 20` or higher to cover more anime series) and fetch corresponding AniList reviews using `python ingest/run_ingest_reviews.py`.
* **Vector Embeddings**:
  * Generated using `intfloat/multilingual-e5-large-instruct` (1024-dim).
  * Similarity searches use the `<=>` operator (cosine distance converted to similarity via `1 - distance`).

---

## 🎨 Code Style & Design Guidelines

* **Frontend CSS**: Use Vanilla CSS variables, responsive design, and standard flex/grid layouts. Avoid TailwindCSS unless explicitly requested.
* **Typography & Vibe**: "Quiet-otaku" theme: restrained accents (`#d6603f`), dark surfaces (`#221f26`), and Japanese typography details (Noto Serif JP/Sans JP).
* **Accessibility**: Maintain a WCAG AA baseline: explicit focus visible outlines, minimum 44px tap targets, and respect `prefers-reduced-motion` settings.
