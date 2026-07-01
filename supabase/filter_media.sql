create or replace function filter_media(
  genre_filter text default null,
  min_episodes int default null,
  max_episodes int default null,
  format_filter text default null,
  limit_count int default 20
)
returns table (title text, metadata jsonb)
language sql stable
as $$
  select title, metadata
  from media_chunks
  where (genre_filter is null or metadata->'genres' ? genre_filter)
    and (min_episodes is null or (metadata->>'episodes')::int >= min_episodes)
    and (max_episodes is null or (metadata->>'episodes')::int <= max_episodes)
    and (format_filter is null or metadata->>'format' = format_filter)
  order by (metadata->>'episodes')::int desc nulls last
  limit limit_count;
$$;
