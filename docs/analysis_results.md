# SenpAI Project Analysis & Findings

This document outlines the architecture, database configurations, evaluation results, and structural recommendations for the SenpAI RAG assistant.

---

## 1. Core Architecture & Stack
* **Frontend**: Pure HTML, Vanilla CSS, and Vanilla JavaScript (`public/`). Implements a responsive chat UI, message scrolling animations, and a collapsible retrieval inspector.
* **Serverless Backend**: A Node.js function (`api/chat.js`) that handles API rate-limiting via Supabase, classifications using Together AI's serverless completions, database RPC lookups, and SSE token streaming.
* **Data Ingest Pipeline**: Python scripts (`ingest/`) designed to query AniList's public GraphQL endpoint, clean description fields, generate 1024-dimensional vectors via Together AI's `intfloat/multilingual-e5-large-instruct`, and upload the results to Supabase.
* **Evaluation Suite**: A local python testing harness (`eval/`) using pre-defined question datasets to evaluate route mapping, source retrieval hits, and exact keyword answer matches.

---

## 2. Ingestion Pipeline & Embedding Details
* **Source Filtering**: Enforces `isAdult: false` at ingestion time to guarantee SFW content.
* **Rate Limits Compliance**: Utilizes a `0.7s` delay between paginated requests to respect AniList API rate limits.
* **Vector Models**: Employs `intfloat/multilingual-e5-large-instruct` (1024 dimensions) for embedding text summaries.
* **Database Size**: Ingested 20 pages from AniList, yielding 1000 anime metadata chunks (plus 747 review chunks).

---

## 3. Database Schema & RPC Functions
The database schema consists of:
* `media_chunks` table containing columns `(id, source, source_id, title, chunk_text, embedding, metadata, created_at)`.
* An `hnsw` index (`media_chunks_embedding_hnsw_idx`) on the `embedding` column using `vector_cosine_ops`.
* `match_media_chunks` SQL RPC function returning rows matching cosine similarity for semantic queries.
* `filter_media` SQL RPC function matching genre, format, or episode filters across the full corpus.

---

## 4. Evaluation Performance Metrics
We verified the production routing and answers using the local virtualenv python interpreter on the expanded 1000-entry database:
* **Regression Dataset (`qa_pairs.json`)**:
  * Route match rate: 100% (22/22)
  * Retrieval hit rate: 86% (19/22)
  * Answer keyword match rate: 86% (19/22)
* **Holdout Dataset (`qa_pairs_holdout.json`)** (historical baseline on 250-entry corpus):
  * Route match rate: 100% (45/45)
  * Retrieval hit rate: 100% (45/45)
  * Answer keyword match rate: 98% (44/45) (The single mismatch is a strict keyword validation artifact on Saitama's unfulfillment, where the model generated "single punch" instead of the expected "one punch").

---

## 5. Security & Rate Limiting
* Rate limits are backed by the Postgres table `rate_limits` via the PL/pgSQL function `check_rate_limit(bucket_key, max_count, window_seconds)`.
* Current limits: 15 requests/min per IP, 1000 requests/day globally.
* The API handler falls open in case of database limiter errors to avoid breaking availability.
* **Jikan (MAL Reviews) API rate limits**: Enforces a strict limit of 3 requests/sec and 60 requests/min. The batch size in `run_ingest_reviews.py` has been adjusted to `60` to respect this limit, but IP-level throttling on public requests remains a bottleneck, causing review ingestion to halt at 250 items when throttling is active.

---

## 6. Recommendations & Optimization Points

### A. SQL Function Signature Discrepancy
* **Observation**: The definition of `filter_media` in `supabase/schema.sql` returns `(source_id text, title text, metadata jsonb)`. However, `supabase/filter_media.sql` omits `source_id` from the return type and introduces a `limit_count` argument.
* **Impact**: If a developer executes `filter_media.sql` on the database, it will break the backend `api/chat.js` since the mapping logic expects `r.source_id` to link references.
* **Resolution**: Standardize `supabase/filter_media.sql` to match `schema.sql`'s return type signature.

### B. Vector Indexing Upgrade (COMPLETED)
* **Observation**: The schema has been updated to use an `hnsw` index (`media_chunks_embedding_hnsw_idx` on `vector_cosine_ops`).
* **Result**: Upgrading from `ivfflat` restored retrieval hit rate from 73% back to 86% and answer accuracy to 95% after corpus expansion to 500 entries.

### C. Answer Evaluation Scoring
* **Observation**: The evaluation keyword match checks for exact substrings.
* **Recommendation**: Adopt fuzzy substring matches or an LLM grader to avoid false negatives on minor keyword variations (e.g. "single punch" vs "one punch").
