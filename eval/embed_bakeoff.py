"""Can a free embedding provider (Cloudflare Workers AI bge-m3) replace e5-large?

Offline proxy, no Qdrant writes: for each semantic_search fixture, build a pool =
top-50 lexical (sparse) hits from live Qdrant + the expected title's own chunks +
a few random chunks, embed the pool + question with bge-m3, and score hit@K with
eval.py's retrieval_hit. Reports sparse-only / dense-only / hybrid (RRF).

Not comparable 1:1 with the production 95% retrieval number: the pool is a
hard-lexical-distractor subset, not the full 68k corpus, and e5 can't be re-run
(its query embeddings are no longer available). Use it as a go/no-go signal.

Needs CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN (Workers AI) in .env.
Usage: ./.venv/bin/python eval/embed_bakeoff.py [--limit N]   (--selftest: offline check)
Embeddings are cached in data/bakeoff_cache.json, so a daily-cap stop resumes next run.
"""
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
sys.path.insert(0, str(ROOT / "eval"))
from eval import retrieval_hit  # noqa: E402
from qdrant_client.models import FieldCondition, Filter, MatchValue  # noqa: E402
from qdrant_store import COLLECTION, SPARSE_NAME, get_client, sparse_vector  # noqa: E402

load_dotenv(ROOT / ".env")

MODEL = "@cf/baai/bge-m3"
K = 5
SPARSE_POOL = 50
RANDOM_POOL = 10
BATCH = 25
RRF_K = 60
CACHE = ROOT / "data" / "bakeoff_cache.json"
FIXTURES = [ROOT / "eval" / "qa_pairs.json", ROOT / "eval" / "qa_pairs_holdout.json"]


def rrf(*rank_lists):
    scores = {}
    for ranks in rank_lists:
        for r, key in enumerate(ranks):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + r + 1)
    return sorted(scores, key=scores.get, reverse=True)


def selftest():
    assert rrf(["a", "b"], ["b", "c"])[0] == "b"
    assert rrf(["a"], [])[0] == "a"
    print("selftest ok")


def load_questions():
    out = []
    for f in FIXTURES:
        for p in json.loads(f.read_text()):
            if p.get("expected_route", "semantic_search") == "semantic_search" and (
                p.get("expected_title") or p.get("expected_titles_any")
            ):
                out.append(p)
    return out


def sparse_hits(client, text, limit):
    sv = sparse_vector(text)
    if not sv.indices:
        return []
    return client.query_points(
        collection_name=COLLECTION,
        query=sv,
        using=SPARSE_NAME,
        limit=limit,
        query_filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value="anilist"))]),
        with_payload=True,
    ).points


def build_pool(client, pair, rng, random_chunks):
    hits = {str(h.id): h for h in sparse_hits(client, pair["question"], SPARSE_POOL)}
    sparse_order = list(hits)
    expected = [t.lower() for t in ([pair["expected_title"]] if pair.get("expected_title") else pair["expected_titles_any"])]
    for t in expected:
        for h in sparse_hits(client, t, SPARSE_POOL):
            if h.payload["title"].lower() == t:
                hits.setdefault(str(h.id), h)
    for h in rng.sample(random_chunks, RANDOM_POOL):
        hits.setdefault(str(h.id), h)
    return hits, sparse_order


def embed(texts, cache):
    key = lambda t: hashlib.sha1((MODEL + t).encode()).hexdigest()
    todo = [t for t in dict.fromkeys(texts) if key(t) not in cache]
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/ai/run/{MODEL}"
    headers = {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"}
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        for attempt in range(4):
            r = requests.post(url, headers=headers, json={"text": batch}, timeout=90)
            if r.status_code == 200:
                break
            if "daily free allocation" in r.text:
                CACHE.write_text(json.dumps(cache))
                sys.exit(f"Daily free cap hit after {i} new texts. Progress cached, rerun tomorrow.\n{r.text[:300]}")
            if r.status_code not in (429, 500, 502, 503) or attempt == 3:
                sys.exit(f"Cloudflare error {r.status_code}: {r.text[:400]}")
            time.sleep(2**attempt)
        vecs = r.json()["result"]["data"]
        assert len(vecs) == len(batch), f"got {len(vecs)} vectors for {len(batch)} texts"
        for t, v in zip(batch, vecs):
            cache[key(t)] = v
        CACHE.write_text(json.dumps(cache))
        print(f"  embedded {min(i + BATCH, len(todo))}/{len(todo)}", flush=True)
    return {t: np.array(cache[key(t)], dtype=np.float32) for t in texts}


def main():
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    for var in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
        if not os.environ.get(var):
            sys.exit(f"Missing {var} in .env (Cloudflare dashboard > Workers AI; token needs Workers AI read/run).")

    client = get_client()
    rng = random.Random(0)
    questions = load_questions()[:limit]
    random_chunks = client.scroll(COLLECTION, limit=500, with_payload=True,
                                  scroll_filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value="anilist"))]))[0]

    pools = [build_pool(client, p, rng, random_chunks) for p in questions]
    texts = [h.payload["chunk_text"] for hits, _ in pools for h in hits.values()] + [p["question"] for p in questions]
    unique = len(set(texts))
    print(f"{len(questions)} questions, {unique} unique texts, ~{sum(len(t) for t in set(texts)) // 4:,} tokens (chars/4 estimate)")

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    vecs = embed(texts, cache)

    tallies = {"sparse": 0, "dense": 0, "hybrid": 0}
    for pair, (hits, sparse_order) in zip(questions, pools):
        ids = list(hits)
        M = np.stack([vecs[hits[i].payload["chunk_text"]] for i in ids])
        q = vecs[pair["question"]]
        sims = (M @ q) / (np.linalg.norm(M, axis=1) * np.linalg.norm(q))
        dense_order = [ids[j] for j in np.argsort(-sims)]
        orders = {"sparse": sparse_order, "dense": dense_order, "hybrid": rrf(dense_order, sparse_order)}
        row = {}
        for name, order in orders.items():
            titles = {hits[i].payload["title"] for i in order[:K]}
            row[name] = retrieval_hit(pair, titles)
            tallies[name] += row[name]
        if not row["dense"] or not row["hybrid"]:
            print(f"MISS dense={row['dense']} hybrid={row['hybrid']} :: {pair['question']}")

    n = len(questions)
    print(f"\nhit@{K} over {n} semantic questions (model {MODEL}):")
    for name, hits_ in tallies.items():
        print(f"  {name:7s} {hits_}/{n} = {hits_ / n:.0%}")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
