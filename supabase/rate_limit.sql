-- Postgres-backed rate limiter for the public /api/chat endpoint.
-- Shared across all serverless instances (in-memory would reset per cold start).
-- Fixed-window counter: deliberate — sliding window is overkill for abuse deterrence.

create table if not exists rate_limits (
  bucket text primary key,
  count int not null default 0,
  window_start timestamptz not null default now()
);

-- Atomic increment-within-window. Row lock on the upsert makes concurrent
-- requests safe. Returns true if the request is allowed (count <= max_count).
create or replace function check_rate_limit(bucket_key text, max_count int, window_seconds int)
returns boolean language plpgsql as $$
declare cur int;
begin
  insert into rate_limits (bucket, count, window_start)
  values (bucket_key, 1, now())
  on conflict (bucket) do update
    set count = case when rate_limits.window_start < now() - make_interval(secs => window_seconds)
                     then 1 else rate_limits.count + 1 end,
        window_start = case when rate_limits.window_start < now() - make_interval(secs => window_seconds)
                     then now() else rate_limits.window_start end
  returning count into cur;
  return cur <= max_count;
end; $$;
