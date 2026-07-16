"""Fetch clean (non-adult) anime metadata from AniList's public GraphQL API."""
import json
import time
from pathlib import Path

import requests

ANILIST_URL = "https://graphql.anilist.co"

QUERY = """
query ($page: Int, $perPage: Int, $type: MediaType, $sort: [MediaSort]) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { hasNextPage }
    media(type: $type, isAdult: false, sort: $sort) {
      id
      idMal
      title { romaji english }
      synonyms
      season
      seasonYear
      description(asHtml: false)
      genres
      tags { name }
      format
      episodes
      chapters
      type
      studios { nodes { name } }
      relations {
        edges {
          relationType
          node {
            title { romaji }
            type
          }
        }
      }
      staff(sort: RELEVANCE, perPage: 10) {
        nodes {
          name { full }
        }
      }
      characters(sort: ROLE, page: 1, perPage: 40) {
        edges {
          role
          node {
            name { full }
            description(asHtml: false)
          }
        }
      }
    }
  }
}
"""


def fetch_page(page: int, media_type: str, per_page: int = 50, sort: str = "POPULARITY_DESC") -> dict:
    for attempt in range(5):
        resp = requests.post(
            ANILIST_URL,
            json={"query": QUERY, "variables": {"page": page, "perPage": per_page, "type": media_type, "sort": [sort]}},
            timeout=30,
        )
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 60)))
            continue
        resp.raise_for_status()
        return resp.json()["data"]["Page"]
    resp.raise_for_status()


def fetch_all(pages: int, media_type: str, per_page: int = 50, sort: str = "POPULARITY_DESC") -> list[dict]:
    entries = []
    for page in range(1, pages + 1):
        data = fetch_page(page, media_type, per_page, sort)
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

    print("Fetching ANIME...")
    anime_entries = fetch_all(args.pages, "ANIME")
    print("Fetching MANGA...")
    manga_entries = fetch_all(args.pages, "MANGA")
    
    entries = anime_entries + manga_entries

    out_path = Path(__file__).parent.parent / "data" / "raw_anilist.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(entries, indent=2))
    print(f"Fetched {len(entries)} total entries ({len(anime_entries)} anime, {len(manga_entries)} manga) -> {out_path}")
