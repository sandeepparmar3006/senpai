"""Fetch MAL user reviews via Jikan for opinion-based questions (second text source)."""
import json
import time
from pathlib import Path

import requests
from tqdm import tqdm

JIKAN_URL = "https://api.jikan.moe/v4/anime/{mal_id}/reviews"
REQUEST_DELAY = 1.1  # Jikan public rate limit is ~60 req/min; 1 req/sec stays safely under it


def fetch_reviews_for(mal_id: int, max_reviews: int = 3, retries: int = 5) -> list[dict]:
    for attempt in range(retries):
        resp = requests.get(JIKAN_URL.format(mal_id=mal_id), params={"page": 1}, timeout=30)
        if resp.status_code == 404:
            return []
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == retries - 1:
                resp.raise_for_status()
            time.sleep(min(2**attempt, 30))
            continue
        resp.raise_for_status()
        return resp.json().get("data", [])[:max_reviews]
    return []


CONSECUTIVE_FAILURE_ABORT = 8  # Jikan's reviews endpoint hard-blocking (not transient flakiness) looks like N straight full-retry exhaustions; no point grinding the rest of the batch once that's happening


def fetch_all(anilist_entries: list[dict], max_reviews: int = 3) -> tuple[list[dict], int, bool]:
    out = []
    skipped = 0
    consecutive_failures = 0
    aborted_early = False
    for entry in tqdm(anilist_entries, desc="fetching reviews"):
        mal_id = entry.get("idMal")
        if not mal_id:
            continue
        title = entry["title"].get("english") or entry["title"].get("romaji")
        try:
            reviews = fetch_reviews_for(mal_id, max_reviews)
            consecutive_failures = 0
        except requests.RequestException:
            reviews = []
            skipped += 1
            consecutive_failures += 1
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
        if consecutive_failures >= CONSECUTIVE_FAILURE_ABORT:
            print(f"  {consecutive_failures} consecutive failures — Jikan looks blocked, aborting batch early")
            aborted_early = True
            break
        time.sleep(REQUEST_DELAY)
    if skipped:
        print(f"  {skipped} entries skipped after exhausting retries")
    return out, skipped, aborted_early


if __name__ == "__main__":
    data_dir = Path(__file__).parent.parent / "data"
    anilist = json.loads((data_dir / "raw_anilist.json").read_text())
    reviews, skipped, aborted_early = fetch_all(anilist)
    (data_dir / "raw_reviews.json").write_text(json.dumps(reviews, indent=2))
    print(f"Fetched {len(reviews)} reviews -> {data_dir / 'raw_reviews.json'}")
