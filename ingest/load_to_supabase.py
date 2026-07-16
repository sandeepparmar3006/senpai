"""Load embedded chunks into Supabase pgvector table."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

BATCH_SIZE = 100


def load(chunks: list[dict], source: str = "anilist") -> int:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    # keyed by source_id: a single upsert batch can't touch the same (source, source_id)
    # conflict target twice (Postgres error 21000), which happens when AniList pagination
    # returns the same entry across pages -- e.g. sorting by a field that changes mid-fetch
    # (UPDATED_AT_DESC in run_freshness_check.py). Last occurrence wins.
    deduped = {
        c["source_id"]: {
            "source": source,
            "source_id": c["source_id"],
            "title": c["title"],
            "chunk_text": c["chunk_text"],
            "embedding": c["embedding"],
            "metadata": c["metadata"],
        }
        for c in chunks
    }
    rows = list(deduped.values())
    for i in range(0, len(rows), BATCH_SIZE):
        client.table("media_chunks").upsert(
            rows[i : i + BATCH_SIZE], on_conflict="source,source_id"
        ).execute()
    return len(rows)


if __name__ == "__main__":
    data_dir = Path(__file__).parent.parent / "data"
    chunks = json.loads((data_dir / "embedded.json").read_text())
    count = load(chunks)
    print(f"Loaded {count} chunks into Supabase")
