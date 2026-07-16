"""Eval harness: retrieval hit rate + answer accuracy across both routing paths."""
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# .get() so the module imports without credentials (e.g. in CI); network calls still require them
TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

EMBED_MODEL = "intfloat/multilingual-e5-large-instruct"
CHAT_MODEL = "openai/gpt-oss-20b"  # open-weight, serverless-accessible on this account
K = 5

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": "Search anime/manga by plot, themes, or synopsis content using semantic similarity. Use for ANY question about a specific named anime's story, characters, powers, or terminology — even if the question starts with 'what' or 'which'. Do not use this to filter or list across multiple anime.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_lookup",
            "description": "Filter the anime database by structured criteria across ALL entries: genre, episode count range, or format. Use ONLY when the question asks to list, count, or filter across multiple anime (e.g. 'what anime have more than N episodes', 'list horror anime', 'which are movies'). Never use this for a question about one specific named anime's plot or details — use semantic_search for that.",
            "parameters": {
                "type": "object",
                "properties": {
                    "genre": {
                        "type": "string",
                        "description": "A single genre to filter by. Case-sensitive — use the exact capitalization from this list.",
                        "enum": ["Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Horror", "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"],
                    },
                    "min_episodes": {"type": "integer"},
                    "max_episodes": {"type": "integer"},
                    "format": {
                        "type": "string",
                        "description": "Exact uppercase format code. If the question asks which entries are 'movies', set this to \"MOVIE\"; 'TV shorts' means TV_SHORT.",
                        "enum": ["TV", "TV_SHORT", "MOVIE", "OVA", "ONA", "SPECIAL", "MUSIC"],
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opinion_search",
            "description": "Search fan reviews for opinion, reception, or recommendation questions about a specific named anime — e.g. 'is X good', 'is X worth watching', 'what do people think of X', 'how is the pacing in X'. Do not use for plot/character/terminology questions (use semantic_search) or whole-corpus filters (use filter_lookup).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


def _post_with_retry(url: str, json_body: dict, attempts: int = 4) -> dict:
    for attempt in range(attempts):
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
                json=json_body,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.RequestException, requests.exceptions.SSLError) as e:
            if attempt == attempts - 1:
                raise
            time.sleep(min(2**attempt, 20))


def _chat_completion(body: dict) -> dict:
    return _post_with_retry("https://api.together.xyz/v1/chat/completions", {"model": CHAT_MODEL, **body})


def route(question: str) -> dict | None:
    data = _chat_completion(
        {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Decide how to answer the user's anime/manga question by calling exactly one tool. "
                        "First check: does the question ask for an opinion, recommendation, rating, or reception about a specific named anime — is it good, is it worth watching, how is the pacing, what do people think, should I watch it? If so, always choose opinion_search, even if it also mentions plot or characters in passing. "
                        "Otherwise, if the question names a specific anime and asks about its plot, characters, or details, choose semantic_search, even if phrased as 'what X'. "
                        "Only choose filter_lookup when the question asks to list, count, or filter across multiple anime by genre, episode count, or format."
                    ),
                },
                {"role": "user", "content": question},
            ],
            "tools": TOOLS,
            "tool_choice": "required",
        }
    )
    tool_calls = data["choices"][0]["message"].get("tool_calls") or []
    return tool_calls[0] if tool_calls else None


def embed_query(text: str) -> list[float]:
    data = _post_with_retry("https://api.together.xyz/v1/embeddings", {"model": EMBED_MODEL, "input": text})
    return data["data"][0]["embedding"]


# Sibling entries (sequels, OVAs, side stories) of the same franchise crowd out
# the top-ranked title's own chunks with near-duplicate header text. Over-fetch
# and cap how many slots other titles can take so the top-ranked title's
# deeper chunks (description, lore) still make it into context.
NONPRIMARY_TITLE_CAP = 2


def _dedupe_sibling_titles(pool: list[dict], k: int) -> list[dict]:
    primary_title = pool[0]["title"] if pool else None
    kept, nonprimary_count = [], 0
    for chunk in pool:
        if len(kept) >= k:
            break
        if chunk["title"] == primary_title:
            kept.append(chunk)
        elif nonprimary_count < NONPRIMARY_TITLE_CAP:
            kept.append(chunk)
            nonprimary_count += 1
    return kept


def semantic_search(query: str, k: int = K, source_filter: str | None = None) -> list[dict]:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    embedding = embed_query(query)
    result = client.rpc(
        "match_media_chunks", {"query_embedding": embedding, "match_count": k * 4, "source_filter": source_filter}
    ).execute()
    return _dedupe_sibling_titles(result.data, k)


def filter_lookup(args: dict) -> list[dict]:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    result = client.rpc(
        "filter_media",
        {
            "genre_filter": args.get("genre"),
            "min_episodes": args.get("min_episodes"),
            "max_episodes": args.get("max_episodes"),
            "format_filter": args.get("format"),
        },
    ).execute()
    return result.data


def build_context(route_name: str, results: list[dict]) -> str:
    if route_name == "filter_lookup":
        total = results[0].get("total_count", len(results)) if results else 0
        header = (
            f'Total matching entries in the database: {total}. Showing {len(results)} below '
            f'(use the total above for any "how many" question, not a count of the list shown).'
        )
        rows = "\n".join(
            f"[{r['title']}] genres: {', '.join(r['metadata'].get('genres') or [])}, "
            f"episodes: {r['metadata'].get('episodes')}, format: {r['metadata'].get('format')}"
            for r in results
        )
        return f"{header}\n{rows}"
    return "\n\n".join(f"[{c['title']}] {c['chunk_text']}" for c in results)


def generate_answer(question: str, route_name: str, results: list[dict]) -> str:
    context = build_context(route_name, results)
    data = _chat_completion(
        {
            "messages": [
                {"role": "system", "content": "Answer only using the provided context. Cite anime titles in brackets."},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
            ]
        }
    )
    return data["choices"][0]["message"]["content"]


def retrieval_hit(pair: dict, retrieved_titles: set[str]) -> bool:
    if pair.get("expected_title"):
        return pair["expected_title"] in retrieved_titles
    if pair.get("expected_titles_any"):
        return bool(retrieved_titles & set(pair["expected_titles_any"]))
    return False


def _norm(text: str) -> str:
    # models sometimes emit unicode spaces (e.g. U+202F in "Pirate King") or unicode dashes
    # (e.g. U+2011 in "K‑ON!") that break exact substring match against ASCII expected keywords
    text = re.sub(r"[‐-―−]", "-", text)
    return re.sub(r"\s+", " ", text.lower())


def keyword_hit(pair: dict, answer: str) -> bool:
    normalized = _norm(answer)
    return any(_norm(kw) in normalized for kw in pair.get("expected_keywords", []))


def run_eval(qa_pairs: list[dict]) -> None:
    route_matches, retrieval_hits, keyword_matches, total = 0, 0, 0, 0
    for pair in qa_pairs:
        if not pair.get("question"):
            continue
        total += 1

        tool_call = route(pair["question"])
        called_name = tool_call["function"]["name"] if tool_call else None
        route_name = called_name if called_name in ("filter_lookup", "opinion_search") else "semantic_search"
        expected_route = pair.get("expected_route", "semantic_search")
        if route_name == expected_route:
            route_matches += 1

        args = json.loads(tool_call["function"]["arguments"]) if tool_call and tool_call["function"].get("arguments") else {}
        if route_name == "filter_lookup":
            results = filter_lookup(args)
            retrieved_titles = {r["title"] for r in results}
        elif route_name == "opinion_search":
            results = semantic_search(args.get("query") or pair["question"], source_filter="jikan_review")
            retrieved_titles = {c["title"] for c in results}
        else:
            results = semantic_search(pair["question"])
            retrieved_titles = {c["title"] for c in results}

        rhit = retrieval_hit(pair, retrieved_titles)
        if rhit:
            retrieval_hits += 1

        answer = generate_answer(pair["question"], route_name, results) if results else "No matching anime found."
        khit = keyword_hit(pair, answer)
        if khit:
            keyword_matches += 1

        flags = []
        if route_name != expected_route:
            flags.append(f"ROUTE expected={expected_route}")
        if not rhit:
            flags.append(f"RETRIEVAL expected={pair.get('expected_title') or pair.get('expected_titles_any')} got={retrieved_titles}")
        if not khit:
            flags.append(f"KEYWORD expected_any={pair.get('expected_keywords')}")
        tag = "FAIL: " + "; ".join(flags) if flags else "PASS"
        print(f"[{tag}] Q: {pair['question']}\n[{route_name}] A: {answer}\n")

    if total == 0:
        print("No filled-in questions in qa_pairs.json yet — nothing to eval.")
        return
    print(f"Route match rate: {route_matches}/{total} = {route_matches/total:.0%}")
    print(f"Retrieval hit rate: {retrieval_hits}/{total} = {retrieval_hits/total:.0%}")
    print(f"Answer keyword match rate: {keyword_matches}/{total} = {keyword_matches/total:.0%}")


if __name__ == "__main__":
    import sys

    qa_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "qa_pairs.json"
    qa_pairs = json.loads(qa_path.read_text())
    run_eval(qa_pairs)
