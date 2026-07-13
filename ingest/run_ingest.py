"""Orchestrate the full ingestion pipeline: fetch -> embed -> load."""
import argparse
import json
from pathlib import Path

from fetch_anilist import fetch_all
from chunk_and_embed import process
from load_to_supabase import load

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=5, help="AniList pages (50 entries each)")
    args = parser.parse_args()

    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    print(f"Fetching {args.pages} pages from AniList...")
    raw_entries = fetch_all(args.pages)
    (data_dir / "raw_anilist.json").write_text(json.dumps(raw_entries, indent=2))
    print(f"  {len(raw_entries)} entries fetched")

    print("Embedding chunks via Together AI...")
    cache_path = data_dir / "embedded.json"
    cache = {}
    if cache_path.exists():
        try:
            cached_data = json.loads(cache_path.read_text())
            cache = {item["source_id"]: {"chunk_text": item.get("chunk_text"), "embedding": item["embedding"]} for item in cached_data}
            print(f"  Loaded {len(cache)} cached embeddings")
        except Exception as e:
            print(f"  Failed to load cache: {e}")

    chunks = process(raw_entries, cache)
    cache_path.write_text(json.dumps(chunks, indent=2))
    print(f"  {len(chunks)} chunks embedded")

    print("Loading into Supabase...")
    count = load(chunks)
    print(f"  {count} rows loaded. Done.")
