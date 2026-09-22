"""Eval harness: retrieval hit rate + answer accuracy across both routing paths."""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "ingest"))
from qdrant_store import get_client as get_qdrant_client  # noqa: E402
from qdrant_store import search as qdrant_search  # noqa: E402
from qdrant_store import filter_query as qdrant_filter_query  # noqa: E402
from qdrant_store import get_main_chunk_by_anilist_id as qdrant_get_main_chunk  # noqa: E402

load_dotenv()

# .get() so the module imports without credentials (e.g. in CI); network calls still require them
TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY")

EMBED_MODEL = "intfloat/multilingual-e5-large-instruct"
CHAT_MODEL = "openai/gpt-oss-120b"  # open-weight, serverless-accessible on this account
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
                    "exclude_genre": {
                        "type": "string",
                        "description": "A single genre to EXCLUDE. Set this when the question says a genre should NOT be included, is excluded, or asks for anime 'that aren't' / 'without' that genre — e.g. 'Comedy anime that aren't Romance' means genre=\"Comedy\", exclude_genre=\"Romance\". Never put the excluded genre in the genre field.",
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
    {
        "type": "function",
        "function": {
            "name": "compare_titles",
            "description": "Compare the episode counts of two specific named anime/manga (e.g. 'does X have fewer episodes than Y', 'which has more episodes, X or Y'). Use ONLY when exactly two titles are named and the comparison is about episode count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_a": {"type": "string", "description": "First anime title"},
                    "title_b": {"type": "string", "description": "Second anime title"},
                },
                "required": ["title_a", "title_b"],
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
                        "Next, if the question names exactly two anime titles and asks to compare their episode counts (e.g. 'does X have more episodes than Y', 'which has more episodes, X or Y'), choose compare_titles. "
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

# A spin-off/OVA's chunk text is often narrower than the canonical entry's
# (tighter description, less cast/plot breadth), which can out-score the
# canonical entry on raw cosine similarity alone even though it's the wrong
# answer. Prefer the most popular (lowest popularity_rank) as "primary"
# rather than trusting pool[0] blindly -- pool is sorted by similarity desc.
# Restricted to titles that share a prefix with pool[0] (same franchise): a
# same-similarity-band global scan once picked an unrelated but more popular
# show (e.g. a Demon Slayer query promoting "Dropkick on My Devil!" as
# primary just because it ranked more popular globally).
def _normalize_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def _is_same_franchise(a: str, b: str) -> bool:
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return False
    max_len = min(len(na), len(nb))
    shared = 0
    while shared < max_len and na[shared] == nb[shared]:
        shared += 1
    return shared >= min(8, max_len)


def _pick_primary_title(pool: list[dict]) -> str | None:
    if not pool:
        return None
    top_title = pool[0]["title"]
    best = pool[0]
    for chunk in pool:
        if not _is_same_franchise(chunk["title"], top_title):
            continue
        best_rank = (best.get("metadata") or {}).get("popularity_rank", float("inf"))
        rank = (chunk.get("metadata") or {}).get("popularity_rank", float("inf"))
        if rank < best_rank:
            best = chunk
    return best["title"]


def _dedupe_sibling_titles(pool: list[dict], k: int) -> list[dict]:
    primary_title = _pick_primary_title(pool)
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
    embedding = embed_query(query)
    pool = qdrant_search(
        get_qdrant_client(), embedding, k=k * 4, source_filter=source_filter, query_text=query
    )
    return _dedupe_sibling_titles(pool, k)


# _resolve_title_facts needs the ONE exact-titled entry, not a diversified top-K --
# _dedupe_sibling_titles' franchise cap can drop it, and k*4=20 isn't always enough
# pool for a short/generic bare title (e.g. "Bleach") to rank inside the top-K
# fused hits at all, even though the exact entry exists in the corpus. Widen the
# pool substantially and skip dedup; _titles_match below is the real filter.
# Mirrors titleLookupPool in api/chat.js.
TITLE_LOOKUP_POOL = 50


def _title_lookup_pool(requested: str) -> list[dict]:
    embedding = embed_query(requested)
    return qdrant_search(
        get_qdrant_client(), embedding, k=TITLE_LOOKUP_POOL, source_filter=None, query_text=requested
    )


# Mirrors compareTitles/resolveTitleFacts/titlesMatch in api/chat.js: validate
# the top hit's title before trusting it, then re-fetch that entry's main
# chunk by anilist_id for the authoritative episode count (a bare title-only
# query's top hit isn't reliably the main chunk -- see chat.js comment).
# Exact match, not substring: "Bleach" is a substring of e.g. "BLEACH:
# Thousand-Year Blood War - The Conflict" (a single 14-episode cour), which
# would silently answer the flagship show's question with the wrong cour's
# count. Failing closed (notFound) beats a plausible-looking wrong number.
def _titles_match(requested: str, hit_title: str) -> bool:
    nr, nh = _normalize_title(requested), _normalize_title(hit_title)
    return bool(nr) and nr == nh


def _resolve_title_facts(requested: str) -> dict:
    hits = _title_lookup_pool(requested)
    # An anime and its manga counterpart can share the exact same title
    # string; "episodes" only applies to the anime one, so prefer it among
    # matches instead of trusting whichever ranked first.
    matches = [h for h in hits if _titles_match(requested, h["title"])]
    hit = next((h for h in matches if (h.get("metadata") or {}).get("type") != "MANGA"), matches[0] if matches else None)
    if not hit:
        return {"requestedTitle": requested, "notFound": True}
    anilist_id = (hit.get("metadata") or {}).get("anilist_id")
    main = qdrant_get_main_chunk(get_qdrant_client(), anilist_id) if anilist_id is not None else None
    source = main or hit
    return {"requestedTitle": requested, "title": source["title"], "metadata": source["metadata"], "similarity": hit["similarity"]}


def compare_titles(title_a: str, title_b: str) -> list[dict]:
    return [_resolve_title_facts(title_a), _resolve_title_facts(title_b)]


def filter_lookup(args: dict) -> list[dict]:
    rows, total_count = qdrant_filter_query(
        get_qdrant_client(),
        genre=args.get("genre"),
        exclude_genre=args.get("exclude_genre"),
        min_episodes=args.get("min_episodes"),
        max_episodes=args.get("max_episodes"),
        format=args.get("format"),
    )
    for r in rows:
        r["total_count"] = total_count
    return rows


def build_context(route_name: str, results: list[dict]) -> str:
    if route_name == "compare_titles":
        lines = []
        for r in results:
            if r.get("notFound"):
                lines.append(f"[{r['requestedTitle']}] Not found in database.")
            else:
                meta = r.get("metadata") or {}
                lines.append(f"[{r['title']}] episodes: {meta.get('episodes', 'unknown')}, format: {meta.get('format', 'unknown')}")
        return "\n".join(lines)
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


GROUNDEDNESS_JUDGE_PROMPT = (
    "You are a strict fact-checker for a RAG system. You will be given the CONTEXT that was "
    "retrieved for a question and the ANSWER the system generated from it. Decide whether every "
    "factual claim in the ANSWER is directly supported by the CONTEXT -- not by outside "
    "knowledge, not by a plausible-sounding inference. A refusal or 'no matching anime found' "
    "answer is always grounded. Respond with exactly one word: GROUNDED or HALLUCINATED."
)


def groundedness_hit(question: str, answer: str, context: str) -> bool:
    # Judge call is skipped when there's no retrieved context (results=[]) --
    # generate_answer already special-cases that to a fixed non-hallucinating
    # string, so there's nothing to fact-check.
    if not context:
        return True
    data = _chat_completion(
        {
            "messages": [
                {"role": "system", "content": GROUNDEDNESS_JUDGE_PROMPT},
                {"role": "user", "content": f"CONTEXT:\n{context}\n\nQuestion: {question}\n\nANSWER: {answer}"},
            ]
        }
    )
    verdict = data["choices"][0]["message"]["content"].strip().upper()
    return "HALLUCINAT" not in verdict


def retrieval_hit(pair: dict, retrieved_titles: set[str]) -> bool:
    retrieved_lower = {t.lower() for t in retrieved_titles}
    # expected_titles_none: negation fixtures (filter_lookup exclude_genre) --
    # any of these showing up means the constraint was dropped or substituted,
    # not just that the positive answer was incomplete. Checked before the
    # positive match so a false-negative can't mask a false-positive.
    if pair.get("expected_titles_none"):
        if retrieved_lower & {t.lower() for t in pair["expected_titles_none"]}:
            return False
    if pair.get("expected_title"):
        return pair["expected_title"].lower() in retrieved_lower
    if pair.get("expected_titles_any"):
        return bool(retrieved_lower & {t.lower() for t in pair["expected_titles_any"]})
    return False


def _norm(text: str) -> str:
    # models sometimes emit unicode spaces (e.g. U+202F in "Pirate King") or unicode dashes
    # (e.g. U+2011 in "K‑ON!") that break exact substring match against ASCII expected keywords
    text = re.sub(r"[‐-―−]", "-", text)
    return re.sub(r"\s+", " ", text.lower())


def keyword_hit(pair: dict, answer: str) -> bool:
    normalized = _norm(answer)
    return any(_norm(kw) in normalized for kw in pair.get("expected_keywords", []))


# Whole-corpus count fixtures (movie count, >200-episode count) go stale every
# time the corpus grows via ingest or the weekly freshness cron -- drifted
# twice already (308->552 movies, 11->13 long-running shows). Rather than
# hardcode a number that silently rots, pairs carry a "live_count" marker and
# get their expected_keywords computed here, fresh, against the live corpus.
LIVE_COUNT_FILTERS = {
    "movies": {"format": "MOVIE"},
    "episodes_over_200": {"min_episodes": 201},
}


def _resolve_live_counts(qa_pairs: list[dict]) -> None:
    cache: dict[str, int] = {}
    for pair in qa_pairs:
        marker = pair.get("live_count")
        if not marker:
            continue
        if marker not in cache:
            filters = LIVE_COUNT_FILTERS[marker]
            _, total = qdrant_filter_query(get_qdrant_client(), **filters)
            cache[marker] = total
        pair["expected_keywords"] = [str(cache[marker])]


# Gate thresholds only cover route/retrieval/keyword -- deliberately NOT
# groundedness, which swung 86%->77% across two back-to-back runs of the
# identical N=22 fixtures on 2026-08-14 (LLM-judge non-determinism, no
# fixed seed). Gating CI on a metric with that much inherent noise would
# fail PRs for no real reason. Thresholds sit below the current baseline
# (100%/95%/95%) with margin for normal variance, but well above what a
# real regression looks like -- the 2026-08-05 routing-model swap that
# crashed route match 100%->64% would still fail this gate.
REGRESSION_THRESHOLDS = {"route": 0.90, "retrieval": 0.85, "keyword": 0.85}


def run_eval(qa_pairs: list[dict], gate: bool = False) -> bool:
    _resolve_live_counts(qa_pairs)
    route_matches, retrieval_hits, keyword_matches, groundedness_hits, total = 0, 0, 0, 0, 0
    for pair in qa_pairs:
        if not pair.get("question"):
            continue
        total += 1

        tool_call = route(pair["question"])
        called_name = tool_call["function"]["name"] if tool_call else None
        route_name = called_name if called_name in ("filter_lookup", "opinion_search", "compare_titles") else "semantic_search"
        expected_route = pair.get("expected_route", "semantic_search")

        args = json.loads(tool_call["function"]["arguments"]) if tool_call and tool_call["function"].get("arguments") else {}
        if route_name == "filter_lookup":
            results = filter_lookup(args)
            retrieved_titles = {r["title"] for r in results}
        elif route_name == "opinion_search":
            results = semantic_search(args.get("query") or pair["question"], source_filter="jikan_review")
            retrieved_titles = {c["title"] for c in results}
        elif route_name == "compare_titles":
            if args.get("title_a") and args.get("title_b"):
                results = compare_titles(args["title_a"], args["title_b"])
            else:
                route_name = "semantic_search"
                results = semantic_search(pair["question"])
            retrieved_titles = {r["title"] for r in results if not r.get("notFound")}
        else:
            results = semantic_search(pair["question"])
            retrieved_titles = {c["title"] for c in results}

        if route_name == expected_route:
            route_matches += 1

        rhit = retrieval_hit(pair, retrieved_titles)
        if rhit:
            retrieval_hits += 1

        answer = generate_answer(pair["question"], route_name, results) if results else "No matching anime found."
        khit = keyword_hit(pair, answer)
        if khit:
            keyword_matches += 1

        context = build_context(route_name, results) if results else ""
        ghit = groundedness_hit(pair["question"], answer, context)
        if ghit:
            groundedness_hits += 1

        flags = []
        if route_name != expected_route:
            flags.append(f"ROUTE expected={expected_route}")
        if not rhit:
            flags.append(f"RETRIEVAL expected={pair.get('expected_title') or pair.get('expected_titles_any')} got={retrieved_titles}")
        if not khit:
            flags.append(f"KEYWORD expected_any={pair.get('expected_keywords')}")
        if not ghit:
            flags.append("HALLUCINATION flagged by judge")
        tag = "FAIL: " + "; ".join(flags) if flags else "PASS"
        print(f"[{tag}] Q: {pair['question']}\n[{route_name}] A: {answer}\n")

    if total == 0:
        print("No filled-in questions in qa_pairs.json yet — nothing to eval.")
        return True
    rates = {
        "route": route_matches / total,
        "retrieval": retrieval_hits / total,
        "keyword": keyword_matches / total,
    }
    print(f"Route match rate: {route_matches}/{total} = {rates['route']:.0%}")
    print(f"Retrieval hit rate: {retrieval_hits}/{total} = {rates['retrieval']:.0%}")
    print(f"Answer keyword match rate: {keyword_matches}/{total} = {rates['keyword']:.0%}")
    print(f"Groundedness rate (LLM judge, no hallucination flagged): {groundedness_hits}/{total} = {groundedness_hits/total:.0%}")

    if not gate:
        return True
    passed = check_gate(rates)
    print("GATE: PASS" if passed else "GATE: FAIL")
    return passed


def check_gate(rates: dict[str, float]) -> bool:
    passed = True
    for name, min_rate in REGRESSION_THRESHOLDS.items():
        if rates[name] < min_rate:
            print(f"GATE FAIL: {name} rate {rates[name]:.0%} below required {min_rate:.0%}")
            passed = False
    return passed


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if a != "--gate"]
    gate = "--gate" in sys.argv[1:]
    qa_path = Path(args[0]) if args else Path(__file__).parent / "qa_pairs.json"
    qa_pairs = json.loads(qa_path.read_text())
    ok = run_eval(qa_pairs, gate=gate)
    if gate and not ok:
        sys.exit(1)
