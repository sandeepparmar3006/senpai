"""Load embedded chunks into Qdrant, replacing the old Supabase pgvector path."""
import os
import time

from dotenv import load_dotenv

from qdrant_store import COLLECTION, ensure_collection, get_client, point_id
from qdrant_store import upsert as qdrant_upsert

load_dotenv()

BATCH_SIZE = 100


def load(chunks: list[dict], source: str = "anilist") -> int:
    client = get_client()
    ensure_collection(client)

    # last-occurrence-wins on source_id, same dedup the old Supabase path did --
    # a single upsert batch can otherwise send the same point ID twice.
    deduped = {c["source_id"]: c for c in chunks}
    rows = list(deduped.values())

    # Preserve existing popularity_rank on refresh instead of overwriting with
    # this run's fetch-order position: run_freshness_check.py sorts by recency
    # (UPDATED_AT_DESC), not popularity, so blindly reassigning rank here would
    # corrupt ranking for exactly the most-actively-updated (often most
    # popular, currently-airing) titles every time the freshness cron runs.
    ids = [point_id(source, c["source_id"]) for c in rows]
    existing = client.retrieve(collection_name=COLLECTION, ids=ids, with_payload=True)
    existing_ranks = {
        pt.payload["source_id"]: pt.payload["metadata"]["popularity_rank"]
        for pt in existing
        if pt.payload.get("metadata", {}).get("popularity_rank") is not None
    }
    for c in rows:
        if c["source_id"] in existing_ranks:
            c.setdefault("metadata", {})["popularity_rank"] = existing_ranks[c["source_id"]]

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        for attempt in range(5):
            try:
                qdrant_upsert(client, batch, source=source)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2**attempt)
    return len(rows)


if __name__ == "__main__":
    import json
    from pathlib import Path

    data_dir = Path(__file__).parent.parent / "data"
    chunks = json.loads((data_dir / "embedded.json").read_text())
    count = load(chunks)
    print(f"Loaded {count} chunks into Qdrant")
