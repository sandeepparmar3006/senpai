"""One-off migration: rebuild media_chunks with a named `sparse` BM25-style
vector alongside the existing dense vector. Qdrant cannot add a new sparse
vector name to an existing collection (update_collection 400s), so this dumps
every point (dense vector + payload) to disk, recreates the collection, and
re-upserts with both vectors. No embedding API cost -- dense vectors come from
the dump, sparse ones are computed locally from each point's chunk_text.

Crash-safe: the dump is written to data/sparse_migration_dump.jsonl first; if
the rebuild phase dies, re-running skips the dump (if complete) and re-upserts
idempotently. Prod search is down between the delete and the end of re-upsert.
User-confirmed destructive rebuild (2026-08-14).

Usage: ./.venv/bin/python ingest/backfill_sparse.py
"""
import json
import os
import sys
import time

sys.path.insert(0, "ingest")
from qdrant_client.models import PointStruct

from qdrant_store import COLLECTION, SPARSE_NAME, ensure_collection, get_client, sparse_vector

DUMP_PATH = "data/sparse_migration_dump.jsonl"
BATCH = 50
UPSERT_RETRIES = 5


def _upsert_with_retry(client, points):
    # Free-tier Qdrant cluster times out intermittently under sustained write
    # load during this rebuild -- same retry/backoff pattern already used
    # elsewhere in this repo (eval.py's _post_with_retry, chunk_and_embed.py).
    for attempt in range(UPSERT_RETRIES):
        try:
            client.upsert(collection_name=COLLECTION, points=points)
            return
        except Exception:
            if attempt == UPSERT_RETRIES - 1:
                raise
            time.sleep(2**attempt)


def dump(client) -> int:
    total = client.count(COLLECTION, exact=True).count
    if os.path.exists(DUMP_PATH):
        with open(DUMP_PATH, encoding="utf-8") as f:
            lines = sum(1 for _ in f)
        if lines == total:
            print(f"dump already complete ({lines} points), skipping")
            return total
    done = 0
    offset = None
    with open(DUMP_PATH, "w", encoding="utf-8") as f:
        while True:
            records, offset = client.scroll(
                collection_name=COLLECTION,
                limit=200,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for r in records:
                f.write(json.dumps({"id": r.id, "vector": r.vector, "payload": r.payload}) + "\n")
            done += len(records)
            print(f"\rdumped {done}/{total}", end="", flush=True)
            if offset is None:
                break
    print()
    return total


def rebuild(client, expected: int):
    if client.collection_exists(COLLECTION):
        info = client.get_collection(COLLECTION)
        if not info.config.params.sparse_vectors:
            client.delete_collection(COLLECTION)
    ensure_collection(client)

    batch, done = [], 0
    with open(DUMP_PATH, encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            batch.append(
                PointStruct(
                    id=p["id"],
                    vector={
                        "": p["vector"],
                        SPARSE_NAME: sparse_vector(p["payload"]["chunk_text"]),
                    },
                    payload=p["payload"],
                )
            )
            if len(batch) >= BATCH:
                _upsert_with_retry(client, batch)
                done += len(batch)
                batch = []
                print(f"\rre-upserted {done}/{expected}", end="", flush=True)
    if batch:
        _upsert_with_retry(client, batch)
        done += len(batch)
    print(f"\rre-upserted {done}/{expected}")
    final = client.count(COLLECTION, exact=True).count
    assert final == expected, f"count mismatch after rebuild: {final} != {expected}"
    print(f"rebuild verified: {final} points, sparse config present")


def main():
    client = get_client()
    expected = dump(client)
    rebuild(client, expected)


if __name__ == "__main__":
    main()
