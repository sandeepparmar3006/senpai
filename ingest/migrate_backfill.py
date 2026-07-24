"""One-time backfill: copy existing media_chunks rows from Supabase into Qdrant.

Assigns metadata.popularity_rank from existing row order (id ascending ==
original AniList POPULARITY_DESC ingestion order, per schema.sql's ordering
comment) since Qdrant's UUID point IDs have no inherent order to replace it.
Idempotent -- safe to re-run, upsert is by deterministic point ID (same
source+source_id always maps to the same point).
"""
import json
import os
import time

from dotenv import load_dotenv
from supabase import create_client
from tqdm import tqdm

from qdrant_store import ensure_collection, get_client, upsert

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

PAGE_SIZE = 500
QDRANT_BATCH_SIZE = 100


def fetch_all_rows(supabase) -> list[dict]:
    rows = []
    start = 0
    while True:
        resp = (
            supabase.table("media_chunks")
            .select("source, source_id, title, chunk_text, embedding, metadata")
            .order("id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        batch = resp.data
        if not batch:
            break
        rows.extend(batch)
        start += PAGE_SIZE
    return rows


def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    qdrant = get_client()
    ensure_collection(qdrant)

    print("Fetching existing media_chunks from Supabase...")
    rows = fetch_all_rows(supabase)
    print(f"Fetched {len(rows)} rows")

    chunks_by_source: dict[str, list[dict]] = {}
    for rank, row in enumerate(rows):
        metadata = dict(row["metadata"] or {})
        metadata["popularity_rank"] = rank
        chunk = {
            "source_id": row["source_id"],
            "title": row["title"],
            "chunk_text": row["chunk_text"],
            "embedding": json.loads(row["embedding"]),  # pgvector comes back as a string over REST
            "metadata": metadata,
        }
        chunks_by_source.setdefault(row["source"], []).append(chunk)

    total = 0
    for source, chunks in chunks_by_source.items():
        for i in tqdm(range(0, len(chunks), QDRANT_BATCH_SIZE), desc=f"upserting {source}"):
            batch = chunks[i : i + QDRANT_BATCH_SIZE]
            for attempt in range(5):
                try:
                    upsert(qdrant, batch, source=source)
                    break
                except Exception:
                    if attempt == 4:
                        raise
                    time.sleep(2**attempt)
        total += len(chunks)
        print(f"{source}: {len(chunks)} rows upserted")

    print(f"Backfilled {total} rows into Qdrant collection 'media_chunks'")


if __name__ == "__main__":
    main()
