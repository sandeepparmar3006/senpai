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


def load(chunks: list[dict]) -> int:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = [
        {
            "source": "anilist",
            "source_id": c["source_id"],
            "title": c["title"],
            "chunk_text": c["chunk_text"],
            "embedding": c["embedding"],
            "metadata": c["metadata"],
        }
        for c in chunks
    ]
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
