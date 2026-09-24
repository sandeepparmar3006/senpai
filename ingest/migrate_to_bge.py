"""One-time: re-embed every point of the old e5 collection with bge-m3 into a NEW collection.

Old collection is only read, never modified. Ids, payloads (incl. popularity_rank) and
sparse vectors carry over; only the dense vector changes. Resumable: progress is
checkpointed after each page, and hitting Cloudflare's free daily cap stops cleanly.

Run (repeat daily until it prints "complete"):
  QDRANT_COLLECTION=media_chunks_bge ./.venv/bin/python ingest/migrate_to_bge.py
"""
import json
import sys
import time
from pathlib import Path

from qdrant_client.models import PointStruct

from cf_embed import DailyCapReached, embed_texts
from qdrant_store import COLLECTION, SPARSE_NAME, ensure_collection, get_client, sparse_vector

OLD = "media_chunks"
PAGE = 100
PROGRESS = Path(__file__).parent.parent / "data" / "bge_migration_progress.json"


def save(state):
    PROGRESS.write_text(json.dumps(state))


def upsert_with_retry(client, points):
    for attempt in range(5):
        try:
            client.upsert(collection_name=COLLECTION, points=points)
            return
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2**attempt)


def main():
    if COLLECTION == OLD:
        sys.exit("Set QDRANT_COLLECTION to the NEW collection name (e.g. media_chunks_bge), not the old one.")
    client = get_client()
    ensure_collection(client)
    total = client.count(OLD, exact=True).count

    state = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {}
    if state.get("target") != COLLECTION:
        state = {"target": COLLECTION, "offset": None, "done": 0, "complete": False}

    while not state["complete"]:
        points, next_offset = client.scroll(OLD, limit=PAGE, offset=state["offset"], with_payload=True, with_vectors=False)
        if points:
            try:
                vecs = embed_texts([p.payload["chunk_text"] for p in points])
            except DailyCapReached as e:
                print(f"Daily free cap reached at {state['done']}/{total}. Rerun tomorrow.\n{e}")
                return
            upsert_with_retry(
                client,
                [
                    PointStruct(id=p.id, vector={"": v, SPARSE_NAME: sparse_vector(p.payload["chunk_text"])}, payload=p.payload)
                    for p, v in zip(points, vecs)
                ],
            )
            state["done"] += len(points)
        state["offset"] = str(next_offset) if next_offset is not None else None
        state["complete"] = next_offset is None
        save(state)
        print(f"{state['done']}/{total}", flush=True)

    new_total = client.count(COLLECTION, exact=True).count
    print(f"complete: old={total} new={new_total} ({'MATCH' if new_total == total else 'MISMATCH, investigate'})")


if __name__ == "__main__":
    main()
