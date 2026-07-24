-- media_chunks, its HNSW index, match_media_chunks(), and filter_media() were
-- dropped 2026-07-24 -- vector storage moved to Qdrant Cloud (see README.md
-- "Vector store migration"). This file now only tracks what stays on
-- Supabase: query-miss logging. Rate limiting lives in rate_limit.sql.

-- Feeds corpus-growth prioritization (ingest/review_misses.py): flags likely
-- corpus gaps so new titles can be ingested by name instead of only by
-- AniList popularity page. is_miss is set at write time in api/chat.js:
-- similarity < 0.83 for semantic_search/opinion_search (empirically, hits on
-- titles that exist in the corpus cluster 0.84-0.90, real gaps 0.80-0.83 --
-- narrow gap, so this is a review-queue signal, not a hard cutoff), or
-- total_count = 0 for filter_lookup.
create table if not exists query_log (
  id bigserial primary key,
  created_at timestamptz default now(),
  question text not null,
  route text not null,
  similarity float,
  result_count int,
  is_miss boolean not null default false
);

create index if not exists query_log_misses_idx
  on query_log (created_at desc) where is_miss;
