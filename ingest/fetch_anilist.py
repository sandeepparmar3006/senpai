"""Fetch clean (non-adult) anime metadata from AniList's public GraphQL API."""
import json
import time
from pathlib import Path

import requests

ANILIST_URL = "https://graphql.anilist.co"

QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(type: ANIME, isAdult: false, sort: POPULARITY_DESC) {
      id
      idMal
      title { romaji english }
      description(asHtml: false)
      genres
      tags { name }
      format
      episodes
      studios { nodes { name } }
      characters(sort: ROLE, page: 1, perPage: 40) {
        nodes {
          name {
            full
          }
        }
      }
    }
  }
}
"""


def fetch_page(page: int, per_page: int = 50) -> dict:
    for attempt in range(5):
        resp = requests.post(
            ANILIST_URL,
            json={"query": QUERY, "variables": {"page": page, "perPage": per_page}},
            timeout=30,
        )
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 60)))
            continue
        resp.raise_for_status()
        return resp.json()["data"]["Page"]
    resp.raise_for_status()


def fetch_all(pages: int, per_page: int = 50) -> list[dict]:
    entries = []
    for page in range(1, pages + 1):
        data = fetch_page(page, per_page)
        entries.extend(data["media"])
        if not data["pageInfo"]["hasNextPage"]:
            break
        time.sleep(0.7)  # stay under AniList's ~90 req/min public rate limit
    return entries


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=5)
    args = parser.parse_args()

    entries = fetch_all(args.pages)
    out_path = Path(__file__).parent.parent / "data" / "raw_anilist.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(entries, indent=2))
    print(f"Fetched {len(entries)} anime entries -> {out_path}")
