"""Fetch MAL user reviews via Jikan for opinion-based questions (second text source)."""
import json
import time
from pathlib import Path

import requests
from tqdm import tqdm

JIKAN_URL = "https://api.jikan.moe/v4/anime/{mal_id}/reviews"
REQUEST_DELAY = 1.1  # Jikan public rate limit is ~60 req/min; 1 req/sec stays safely under it


def fetch_reviews_for(mal_id: int, max_reviews: int = 3) -> list[dict]:
    resp = requests.get(JIKAN_URL.format(mal_id=mal_id), params={"page": 1}, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json().get("data", [])[:max_reviews]


def fetch_all(anilist_entries: list[dict], max_reviews: int = 3) -> list[dict]:
    out = []
    for entry in tqdm(anilist_entries, desc="fetching reviews"):
        mal_id = entry.get("idMal")
        if not mal_id:
            continue
        title = entry["title"].get("english") or entry["title"].get("romaji")
        try:
            reviews = fetch_reviews_for(mal_id, max_reviews)
        except requests.RequestException:
            reviews = []
        for r in reviews:
            out.append(
                {
                    "anilist_id": entry["id"],
                    "mal_id": mal_id,
                    "title": title,
                    "review_id": r["mal_id"],
                    "score": r.get("score"),
                    "review": r.get("review", ""),
                }
            )
        time.sleep(REQUEST_DELAY)
    return out


if __name__ == "__main__":
    data_dir = Path(__file__).parent.parent / "data"
    anilist = json.loads((data_dir / "raw_anilist.json").read_text())
    reviews = fetch_all(anilist)
    (data_dir / "raw_reviews.json").write_text(json.dumps(reviews, indent=2))
    print(f"Fetched {len(reviews)} reviews -> {data_dir / 'raw_reviews.json'}")
