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

create index if not exists media_chunks_embedding_idx
  on media_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create or replace function match_media_chunks(query_embedding vector(1024), match_count int default 5)
returns table (id bigint, source_id text, title text, chunk_text text, metadata jsonb, similarity float)
language sql stable
as $$
  select id, source_id, title, chunk_text, metadata, 1 - (embedding <=> query_embedding) as similarity
  from media_chunks
  order by embedding <=> query_embedding
  limit match_count;
$$;
