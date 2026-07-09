"""Fetch MAL reviews for already-ingested anime and load them as a second source (opinion_search)."""
import json
from pathlib import Path

from fetch_jikan_reviews import fetch_all
from chunk_and_embed_reviews import process
from load_to_supabase import load

if __name__ == "__main__":
    data_dir = Path(__file__).parent.parent / "data"
    anilist = json.loads((data_dir / "raw_anilist.json").read_text())

    print("Fetching MAL reviews via Jikan...")
    reviews = fetch_all(anilist)
    (data_dir / "raw_reviews.json").write_text(json.dumps(reviews, indent=2))
    print(f"  {len(reviews)} reviews fetched")

    print("Embedding review chunks via Together AI...")
    chunks = process(reviews)
    (data_dir / "embedded_reviews.json").write_text(json.dumps(chunks, indent=2))
    print(f"  {len(chunks)} chunks embedded")

    print("Loading into Supabase (source=jikan_review)...")
    count = load(chunks, source="jikan_review")
    print(f"  {count} rows loaded. Done.")
