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

250 anime entries ingested. 22-question hand-labeled eval set — 8 metadata lookups, 12 synopsis/plot questions (semantic route), 2 structured questions (filter route, e.g. "what anime have more than 150 episodes"). `eval.py` now runs every question through the same router as production (real tool-calling, not a hardcoded route per question):

- Route match rate (router picked the expected path): 18/22 = 82%
- Retrieval hit rate: 17/22 = 77%
- Answer keyword match rate: 17/22 = 77%

Honest read: the router is not perfect, and that's the real finding, not a bug to hide. Two concrete failure modes observed and reproducible via `python eval/eval.py`:
1. Plot questions occasionally get misrouted to `filter_lookup` (e.g. "what creatures devour humans in Attack on Titan" — a synopsis question — got classified as structured and returned "No matching anime found").
2. `filter_lookup` sometimes extracts wrong or empty arguments for legitimate structured questions (the "which anime are movies" question returned no results — the model didn't pass `format: "MOVIE"` correctly).

The earlier 100%/100% numbers (previous README revision) were measured before the router existed, i.e. semantic-search-only. Adding the router improved real correctness (structured questions now get accurate whole-corpus answers instead of top-5-similarity guesses) but introduced routing error as a new, measurable failure surface — the tradeoff is real and this is what "I added an eval and it changed my story" actually looks like.

## Resume bullet

> Built SenpAI, a production RAG assistant over 250+ anime/manga entries with a tool-routing layer (semantic search vs. structured SQL filter) chosen via real function-calling, by combining AniList metadata ingestion, Together AI embeddings/inference, and Supabase pgvector retrieval — deployed live on Vercel at senpai-seven.vercel.app. Eval harness (22 hand-labeled questions) surfaced and documents a genuine 82% routing accuracy, not an inflated number.

## Phase 2

- ~~Function-calling / tool-routing layer (RAG vs. structured lookup)~~ — done. `api/chat.js` routes between `semantic_search` and `filter_lookup` via real tool-calling.
- ~~Eval coverage for the routing layer~~ — done. `eval.py` now routes every question the same way production does; see real 82%/77%/77% numbers above.
- **Next real improvement, not done:** tighten the router's system prompt / tool descriptions to reduce misclassification, then re-run eval to see if the number actually moves — don't just re-word the prompt and assume it helped.
- Jikan/MyAnimeList reviews as a second text source (richer opinion-based questions) — not started.
