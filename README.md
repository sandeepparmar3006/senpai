# SenpAI

[![CI](https://github.com/sandeepparmar3006/senpai/actions/workflows/ci.yml/badge.svg)](https://github.com/sandeepparmar3006/senpai/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

RAG assistant over anime/manga metadata with a **function-calling tool router** — every answer is grounded in retrieved sources and shows which retrieval path it took.

**Live demo: [senpai-seven.vercel.app](https://senpai-seven.vercel.app)**

| Semantic route | Structured route |
|---|---|
| ![Semantic search answering a plot/metadata question with source citations](docs/screenshot-semantic.png) | ![Structured lookup scanning the whole corpus for episode-count filters](docs/screenshot-filter.png) |

## Why a router

Top-k similarity search silently fails on whole-corpus questions: "which anime have more than 150 episodes?" needs to scan all 250 rows, not the 5 most similar chunks. So the model picks a tool per question via real function-calling (`tool_choice: required`), not a manual classifier prompt:

- `semantic_search` — embed the query, pgvector `match_media_chunks()` RPC (plot/synopsis questions)
- `filter_lookup` — `filter_media()` SQL RPC over all rows (genre/episode/format filters, lists, counts)

One production detail worth knowing: open-weight models sometimes emit a hallucinated answer in `message.content` *alongside* the real `tool_calls`. The implementation discards `content` and only trusts the executed tool result.

## Eval results

22 hand-labeled questions (8 metadata, 12 plot, 2 structured), run through the **same router as production** by `eval/eval.py`:

| Stage | Route match | Retrieval hit | Answer match |
|---|---|---|---|
| Pre-router (semantic-only baseline) | — | 100% | 100% |
| Router added | 82% | 77% | 77% |
| After router fix (`45c27b5`) | 100% | 100% | 100% |

Adding the router improved real correctness (structured questions get accurate whole-corpus answers instead of top-5 guesses) but introduced routing error as a new, measurable failure surface. The eval caught two reproducible failure modes:

1. Plot questions occasionally misrouted to `filter_lookup` ("what creatures devour humans in Attack on Titan" was classified as structured and returned nothing).
2. `filter_lookup` sometimes extracted wrong or empty arguments ("which anime are movies" didn't pass `format: "MOVIE"`).

Both traced to the same root cause: tool descriptions didn't state the disambiguation rule (named-title plot question wins even when phrased as "what X") and the `format` param had no enum. Fixed both, re-ran the eval unchanged: 100/100/100. That re-run is a regression check validating those two fixes — the next step is a larger held-out set the fixes weren't tuned against, to test generalization.

Two smaller findings from repeat runs, kept because they're what eval work actually looks like:

- **Routing is sampled**, so route match occasionally drops a question run-to-run (21/22 observed on one re-run). Single-run numbers on N=22 carry real variance.
- **One "failure" was a scoring bug, not a model bug**: the model emitted a U+202F narrow no-break space inside "Pirate King", which broke exact substring matching. `keyword_hit()` now normalizes unicode whitespace, with a regression test in `tests/test_eval.py`.

## Architecture

```
AniList GraphQL (isAdult: false filtered at fetch time)
        |
   ingest/fetch_anilist.py       -> data/raw_anilist.json
        |
   ingest/chunk_and_embed.py     -> data/embedded.json
        | (Together AI embeddings, intfloat/multilingual-e5-large-instruct, 1024-dim)
   ingest/load_to_supabase.py    -> Supabase pgvector table
        |
   api/chat.js (Vercel function)
        | route(query) -> Together chat completion w/ tools (openai/gpt-oss-20b), tool_choice: required
        |   |-- semantic_search  -> embed query -> match_media_chunks() RPC
        |   |-- filter_lookup    -> filter_media() RPC
        | generate(question, route_results) -> Together chat completion w/ citations
   public/ (chat UI, shows which route was taken)
```

Corpus is SFW: the AniList fetch query hard-filters `isAdult: false`, so adult-tagged entries never enter the pipeline.

Model note: `BAAI/bge-*` embeddings and `meta-llama/Llama-3.3-*-Free` chat models are catalog-listed on Together but require a paid dedicated endpoint — the models above were confirmed serverless-accessible by testing directly against the API.

## Setup

1. **Supabase**: create a project, run `supabase/schema.sql` in the SQL editor. Grab the project URL + service role key (Settings > API).
2. **Together AI**: sign up at https://api.together.xyz, generate an API key (free-tier credits).
3. Copy `.env.example` to `.env` (ingestion) and `.env.local` (Vercel), fill in `TOGETHER_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
4. `pip install -r requirements.txt`
5. `python ingest/run_ingest.py --pages 5` (5 pages x 50 = 250 anime entries)
6. `python eval/eval.py` — routes every question through the production router, prints route/retrieval/answer rates.
7. `npm install && vercel dev` locally, `vercel --prod` to deploy.

## Roadmap

- **Held-out eval set** (40–50 questions the router fix wasn't tuned against) — tests whether the routing fix generalizes.
- Jikan/MyAnimeList reviews as a second text source for opinion-based questions.

## License

MIT
