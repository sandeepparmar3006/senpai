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
        | embed query (Together) -> match_media_chunks() RPC -> Together chat (openai/gpt-oss-20b) w/ citations
   public/ (minimal chat UI)
```

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

## Phase 2 (only if time remains)

- Jikan/MyAnimeList reviews as a second text source (richer opinion-based questions)
- Function-calling / tool-routing layer (RAG vs. structured lookup)
