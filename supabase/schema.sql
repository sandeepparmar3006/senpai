create extension if not exists vector;

drop table if exists media_chunks;

create table media_chunks (
  id bigserial primary key,
  source text not null default 'anilist',
  source_id text not null,
  title text not null,
  chunk_text text not null,
  embedding vector(1024),
  metadata jsonb,
  created_at timestamptz default now(),
  unique (source, source_id)
);

create index if not exists media_chunks_embedding_hnsw_idx
  on media_chunks using hnsw (embedding vector_cosine_ops);

create or replace function match_media_chunks(
  query_embedding vector(1024),
  match_count int default 5,
  source_filter text default null
)
returns table (id bigint, source_id text, title text, chunk_text text, metadata jsonb, similarity float)
language sql stable
as $$
  select id, source_id, title, chunk_text, metadata, 1 - (embedding <=> query_embedding) as similarity
  from media_chunks
  where source_filter is null or source = source_filter
  order by embedding <=> query_embedding
  limit match_count;
$$;

-- Structured whole-corpus filter used by the filter_lookup tool (api/chat.js).
-- Arg names must match the RPC call in filterLookup(); all filters optional.
create or replace function filter_media(
  genre_filter text default null,
  min_episodes int default null,
  max_episodes int default null,
  format_filter text default null
)
returns table (source_id text, title text, metadata jsonb)
language sql stable
as $$
  select source_id, title, metadata
  from media_chunks
  where (genre_filter is null or metadata->'genres' ? genre_filter)
    and (min_episodes is null or (metadata->>'episodes')::int >= min_episodes)
    and (max_episodes is null or (metadata->>'episodes')::int <= max_episodes)
    and (format_filter is null or metadata->>'format' = format_filter)
  order by (metadata->>'episodes')::int desc nulls last
  limit 20;
$$;
