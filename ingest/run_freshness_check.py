"""Weekly freshness pass: catches new releases and corrected metadata on existing
entries in one query, using AniList's UPDATED_AT_DESC sort (an entry's updatedAt
bumps whether it's brand new or just edited). Reuses run_ingest.py's cache-and-upsert
pipeline, so unchanged chunks are never re-embedded and existing rows are updated
in place via the (source, source_id) unique constraint.
"""
import json
from pathlib import Path

from fetch_anilist import fetch_all
from chunk_and_embed import process
from load_to_supabase import load

PAGES = 3  # 150 most recently updated entries/week; raise if weekly volume grows

if __name__ == "__main__":
    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    print(f"Fetching {PAGES} pages of recently-updated ANIME from AniList...")
    anime_entries = fetch_all(PAGES, "ANIME", sort="UPDATED_AT_DESC")
    print(f"Fetching {PAGES} pages of recently-updated MANGA from AniList...")
    manga_entries = fetch_all(PAGES, "MANGA", sort="UPDATED_AT_DESC")

    raw_entries = anime_entries + manga_entries
    print(f"  {len(raw_entries)} entries fetched ({len(anime_entries)} anime, {len(manga_entries)} manga)")

    cache_path = data_dir / "embedded.json"
    cache = {}
    if cache_path.exists():
        try:
            cached_data = json.loads(cache_path.read_text())
            cache = {item["source_id"]: {"chunk_text": item.get("chunk_text"), "embedding": item["embedding"]} for item in cached_data}
        except Exception as e:
            print(f"  Failed to load cache: {e}")

    print("Embedding changed/new chunks via Together AI...")
    chunks = process(raw_entries, cache)
    cache.update({c["source_id"]: {"chunk_text": c["chunk_text"], "embedding": c["embedding"]} for c in chunks})
    cache_path.write_text(json.dumps([{"source_id": k, **v} for k, v in cache.items()], indent=2))

    print("Upserting into Supabase...")
    count = load(chunks)
    print(f"  {count} rows upserted. Done.")
