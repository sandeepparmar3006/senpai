"""Eval harness: retrieval hit rate + answer accuracy across both routing paths."""
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

TOGETHER_API_KEY = os.environ["TOGETHER_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

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
                    "genre": {"type": "string"},
                    "min_episodes": {"type": "integer"},
                    "max_episodes": {"type": "integer"},
                    "format": {
                        "type": "string",
                        "description": "Exact uppercase format code: TV, MOVIE, OVA, ONA, SPECIAL, or MUSIC. If the question asks which entries are 'movies', set this to \"MOVIE\".",
                    },
                },
            },
        },
    },
]


def _chat_completion(body: dict) -> dict:
    resp = requests.post(
        "https://api.together.xyz/v1/chat/completions",
        headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
        json={"model": CHAT_MODEL, **body},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def route(question: str) -> dict | None:
    data = _chat_completion(
        {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Decide how to answer the user's anime/manga question by calling exactly one tool. "
                        "If the question names a specific anime and asks about its plot, characters, or details, always choose semantic_search, even if phrased as 'what X'. "
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
    resp = requests.post(
        "https://api.together.xyz/v1/embeddings",
        headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
        json={"model": EMBED_MODEL, "input": text},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def semantic_search(query: str, k: int = K) -> list[dict]:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    embedding = embed_query(query)
    result = client.rpc("match_media_chunks", {"query_embedding": embedding, "match_count": k}).execute()
    return result.data


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
        return "\n".join(
            f"[{r['title']}] genres: {', '.join(r['metadata'].get('genres') or [])}, "
            f"episodes: {r['metadata'].get('episodes')}, format: {r['metadata'].get('format')}"
            for r in results
        )
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


def run_eval(qa_pairs: list[dict]) -> None:
    route_matches, retrieval_hits, keyword_matches, total = 0, 0, 0, 0
    for pair in qa_pairs:
        if not pair.get("question"):
            continue
        total += 1

        tool_call = route(pair["question"])
        route_name = "filter_lookup" if tool_call and tool_call["function"]["name"] == "filter_lookup" else "semantic_search"
        expected_route = pair.get("expected_route", "semantic_search")
        if route_name == expected_route:
            route_matches += 1

        if route_name == "filter_lookup":
            args = json.loads(tool_call["function"]["arguments"]) if tool_call and tool_call["function"].get("arguments") else {}
            results = filter_lookup(args)
            retrieved_titles = {r["title"] for r in results}
        else:
            results = semantic_search(pair["question"])
            retrieved_titles = {c["title"] for c in results}

        if pair.get("expected_title"):
            if pair["expected_title"] in retrieved_titles:
                retrieval_hits += 1
        elif pair.get("expected_titles_any"):
            if retrieved_titles & set(pair["expected_titles_any"]):
                retrieval_hits += 1

        answer = generate_answer(pair["question"], route_name, results) if results else "No matching anime found."
        if any(kw.lower() in answer.lower() for kw in pair.get("expected_keywords", [])):
            keyword_matches += 1
        print(f"Q: {pair['question']}\n[{route_name}] A: {answer}\n")

    if total == 0:
        print("No filled-in questions in qa_pairs.json yet — nothing to eval.")
        return
    print(f"Route match rate: {route_matches}/{total} = {route_matches/total:.0%}")
    print(f"Retrieval hit rate: {retrieval_hits}/{total} = {retrieval_hits/total:.0%}")
    print(f"Answer keyword match rate: {keyword_matches}/{total} = {keyword_matches/total:.0%}")


if __name__ == "__main__":
    qa_path = Path(__file__).parent / "qa_pairs.json"
    qa_pairs = json.loads(qa_path.read_text())
    run_eval(qa_pairs)
