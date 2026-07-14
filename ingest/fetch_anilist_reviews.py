"""Fetch user reviews via AniList GraphQL API for opinion-based questions (second text source)."""
import json
import time
from pathlib import Path

import requests
from tqdm import tqdm

ANILIST_URL = "https://graphql.anilist.co"
REQUEST_DELAY = 0.7  # AniList public rate limit is ~90 req/min

QUERY = """
query ($idIn: [Int]) {
  Page(page: 1, perPage: 50) {
    media(id_in: $idIn) {
      id
      reviews(sort: SCORE_DESC, perPage: 6) {
        nodes {
          id
          body(asHtml: false)
          score
        }
      }
    }
  }
}
"""

def fetch_reviews_batch(anilist_ids: list[int], retries: int = 5) -> dict:
    for attempt in range(retries):
        resp = requests.post(
            ANILIST_URL,
            json={"query": QUERY, "variables": {"idIn": anilist_ids}},
            timeout=30,
        )
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 60)))
            continue
        if resp.status_code >= 500:
            if attempt == retries - 1:
                resp.raise_for_status()
            time.sleep(min(2**attempt, 30))
            continue
        resp.raise_for_status()
        
        # map media ID to its reviews
        media_nodes = resp.json()["data"]["Page"]["media"]
        return {m["id"]: m["reviews"]["nodes"] for m in media_nodes}
    return {}


def fetch_all(anilist_entries: list[dict], max_reviews: int = 6) -> tuple[list[dict], int, bool]:
    out = []
    skipped = 0
    aborted_early = False
    
    # AniList allows up to 50 items per page
    chunk_size = 50
    for i in tqdm(range(0, len(anilist_entries), chunk_size), desc="fetching reviews"):
        chunk = anilist_entries[i:i + chunk_size]
        ids = [entry["id"] for entry in chunk]
        
        try:
            reviews_by_media = fetch_reviews_batch(ids)
        except requests.RequestException:
            skipped += len(chunk)
            # If batch completely fails after all retries, might be blocked
            aborted_early = True
            break
            
        for entry in chunk:
            media_id = entry["id"]
            reviews = reviews_by_media.get(media_id, [])[:max_reviews]
            
            title = entry["title"].get("english") or entry["title"].get("romaji")
            mal_id = entry.get("idMal")
            
            for r in reviews:
                out.append(
                    {
                        "anilist_id": media_id,
                        "mal_id": mal_id,  # Can be None if AniList entry has no MAL ID mapped
                        "title": title,
                        "review_id": r["id"],
                        "score": r.get("score", 0) / 10 if r.get("score") is not None else None, # Convert 0-100 to 0-10
                        "review": r.get("body", ""),
                    }
                )
        
        time.sleep(REQUEST_DELAY)
        
    return out, skipped, aborted_early


if __name__ == "__main__":
    data_dir = Path(__file__).parent.parent / "data"
    anilist = json.loads((data_dir / "raw_anilist.json").read_text())
    reviews, skipped, aborted_early = fetch_all(anilist)
    (data_dir / "raw_reviews.json").write_text(json.dumps(reviews, indent=2))
    print(f"Fetched {len(reviews)} reviews -> {data_dir / 'raw_reviews.json'}")
