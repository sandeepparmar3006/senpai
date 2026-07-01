# SenpAI

RAG assistant over clean anime/manga metadata (synopses, genres, tags, studios) — no adult content, no reused code from any other project.

## Architecture

```
AniList GraphQL (isAdult: false filter at fetch time)
        |
   ingest/fetch_anilist.py       -> data/raw_anilist.json
        |
   ingest/chunk_and_embed.py     -> data/embedded.json
        | (Together AI embeddings, intfloat/multilingual-e5-large-instruct, 1024-dim)
   ingest/load_to_supabase.py    -> Supabase pgvector table
        |
   api/chat.js (Vercel function)
        | route(query) -> Together chat completion w/ tools (openai/gpt-oss-20b), tool_choice: required
        |   |-- semantic_search  -> embed query -> match_media_chunks() RPC (plot/synopsis questions)
        |   |-- filter_lookup    -> filter_media() RPC (structured: genre/episodes/format across ALL rows)
        | generate(question, route_results) -> Together chat completion w/ citations
   public/ (chat UI, shows which route was taken)
```

Tool-routing layer: `semantic_search` only returns the top-5 similar chunks, which silently gives wrong/incomplete answers for questions like "what anime have more than 100 episodes" (needs to scan all 250 rows, not top-5 by similarity). The router forces the model to pick `semantic_search` or `filter_lookup` per question via real function-calling (not a manual classifier prompt) before answering. Verified: open-weight models sometimes emit a hallucinated answer in `message.content` alongside the real `tool_calls` — the implementation discards `content` and only trusts the executed tool result.

Note: `BAAI/bge-*` embedding models and `meta-llama/Llama-3.3-*-Free` chat models require a paid dedicated endpoint on Together — not available on free-tier accounts despite being listed in the catalog. Stuck to models confirmed serverless-accessible by testing directly against the API.

## Setup

1. **Supabase**: create a project, open SQL editor, run `supabase/schema.sql`. Grab the project URL + service role key (Settings > API).
2. **Together AI**: sign up at https://api.together.xyz, generate an API key (free-tier credits).
3. Copy `.env.example` to `.env` (ingestion) and `.env.local` (Vercel), fill in `TOGETHER_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
4. `pip install -r requirements.txt`
5. `python ingest/run_ingest.py --pages 5` (5 pages x 50 = 250 anime entries to start; bump later)
6. Fill in `eval/qa_pairs.json` with real questions once you know what got ingested (don't guess trivia before the corpus exists — see note in that file).
7. `python eval/eval.py` — prints precision@k + answer-match rate.
8. `npm install && vercel dev` locally, then `vercel --prod` to deploy.

## Content policy

Fetch query hard-filters `isAdult: false` at the AniList API level — adult-tagged entries never enter the pipeline. This is a deliberate, from-scratch corpus with no connection to any other project.

## Eval results

250 anime entries ingested. 20-question hand-labeled eval set — 8 metadata lookups (genre, episode count, format) + 12 synopsis/plot-based questions requiring semantic retrieval (e.g. "what device controls avatars in Sword Art Online," "what forbidden act do the Elric brothers attempt"):
- Retrieval hit rate (expected title in top-5): 20/20 = 100%
- Answer keyword match rate: 20/20 = 100%

Honest caveat: these are single-hop questions against a small (250-entry), well-known-title corpus — real interview follow-ups to expect: "what breaks at 10k+ entries," "what about ambiguous/multi-title queries," "what's your fallback when retrieval returns nothing relevant." 100% here means the pipeline is correct, not that it's stress-tested at scale.

## Resume bullet

> Built SenpAI, a production RAG assistant over 250+ anime/manga entries (Y: 100% retrieval hit rate and answer accuracy across a 20-question eval set spanning metadata and plot-based questions), by combining AniList metadata ingestion, Together AI embeddings/inference, and Supabase pgvector retrieval — deployed live on Vercel at senpai-seven.vercel.app.

## Phase 2

- ~~Function-calling / tool-routing layer (RAG vs. structured lookup)~~ — done. `api/chat.js` routes between `semantic_search` and `filter_lookup` via real tool-calling.
- **Known gap:** `eval/eval.py` still only exercises the `semantic_search` path — it was not updated to mirror the routing logic (cut for time/cost). Manually verified both routes via curl against real ingested data (see commit history), but there's no automated eval coverage for `filter_lookup` yet.
- Jikan/MyAnimeList reviews as a second text source (richer opinion-based questions) — not started.
