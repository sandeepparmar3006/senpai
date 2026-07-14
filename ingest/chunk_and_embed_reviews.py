"""Turn raw AniList reviews into embedded chunks via Together AI (opinion-search source)."""
import json
from pathlib import Path

import requests
from tqdm import tqdm

from chunk_and_embed import embed_text

MAX_CHARS = 1500


def truncate(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS].rsplit(" ", 1)[0]


def build_chunk_text(r: dict) -> str:
    score = r.get("score")
    return f"Review of {r['title']} (score: {score if score is not None else 'n/a'}/10):\n{truncate(r['review'])}"


def process(raw_reviews: list[dict]) -> list[dict]:
    chunks = []
    for r in tqdm(raw_reviews, desc="embedding reviews"):
        if not r.get("review", "").strip():
            continue
        text = build_chunk_text(r)
        try:
            embedding = embed_text(text)
        except requests.exceptions.RequestException as e:
            print(f"  skipping review {r['mal_id']}-{r['review_id']}: {e}")
            continue
        chunks.append(
            {
                "source_id": f"{r['mal_id']}-{r['review_id']}",
                "title": r["title"],
                "chunk_text": text,
                "embedding": embedding,
                "metadata": {"anilist_id": r["anilist_id"], "mal_id": r["mal_id"], "score": r.get("score")},
            }
        )
    return chunks


if __name__ == "__main__":
    data_dir = Path(__file__).parent.parent / "data"
    raw = json.loads((data_dir / "raw_reviews.json").read_text())
    embedded = process(raw)
    (data_dir / "embedded_reviews.json").write_text(json.dumps(embedded, indent=2))
    print(f"Embedded {len(embedded)} review chunks -> {data_dir / 'embedded_reviews.json'}")
