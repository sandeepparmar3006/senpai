"""Shared Qdrant helpers for the media_chunks vector store.

Used by the ingest load path and eval/eval.py, so retrieval logic (K, filter
semantics, point-ID scheme) lives in one place instead of drifting between
callers -- mirrored on the JS side by api/qdrantStore.js.
"""
import os
import uuid

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Direction,
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    OrderBy,
    PayloadSchemaType,
    PointStruct,
    Range,
    VectorParams,
)

load_dotenv()

# .get() so the module imports without credentials (e.g. in CI); network calls still require them
QDRANT_URL = os.environ.get("QDRANT_URL")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
COLLECTION = "media_chunks"
VECTOR_SIZE = 1024

# Fixed namespace for deterministic point IDs -- must never change, or every
# existing point's ID shifts and re-ingest stops matching existing points.
_NAMESPACE = uuid.UUID("a26e9f2e-6e21-4f0b-9c2f-7e9c9f6b7a1e")

PAYLOAD_INDEXES = {
    "source": PayloadSchemaType.KEYWORD,
    "metadata.genres": PayloadSchemaType.KEYWORD,
    "metadata.format": PayloadSchemaType.KEYWORD,
    "metadata.episodes": PayloadSchemaType.INTEGER,
    "metadata.anilist_id": PayloadSchemaType.INTEGER,
    "metadata.popularity_rank": PayloadSchemaType.INTEGER,
}


def point_id(source: str, source_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{source}:{source_id}"))


def get_client() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def ensure_collection(client: QdrantClient | None = None) -> None:
    """Create the collection + payload indexes if missing. Safe to call repeatedly."""
    client = client or get_client()
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
    existing = client.get_collection(COLLECTION).payload_schema
    for field_name, schema in PAYLOAD_INDEXES.items():
        if field_name not in existing:
            client.create_payload_index(
                collection_name=COLLECTION, field_name=field_name, field_schema=schema
            )


def upsert(client: QdrantClient, chunks: list[dict], source: str) -> int:
    # last-occurrence-wins on source_id, same as the old Supabase load path --
    # a single upsert batch can otherwise send the same point ID twice.
    deduped = {c["source_id"]: c for c in chunks}
    points = [
        PointStruct(
            id=point_id(source, c["source_id"]),
            vector=c["embedding"],
            payload={
                "source": source,
                "source_id": c["source_id"],
                "title": c["title"],
                "chunk_text": c["chunk_text"],
                "metadata": c["metadata"],
            },
        )
        for c in deduped.values()
    ]
    client.upsert(collection_name=COLLECTION, points=points)
    return len(points)


def search(
    client: QdrantClient, query_embedding: list[float], k: int = 5, source_filter: str | None = None
) -> list[dict]:
    query_filter = None
    if source_filter is not None:
        query_filter = Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=source_filter))]
        )
    hits = client.query_points(
        collection_name=COLLECTION,
        query=query_embedding,
        limit=k,
        query_filter=query_filter,
    ).points
    return [
        {
            "id": h.id,
            "source_id": h.payload["source_id"],
            "title": h.payload["title"],
            "chunk_text": h.payload["chunk_text"],
            "metadata": h.payload["metadata"],
            "similarity": h.score,
        }
        for h in hits
    ]


def filter_query(
    client: QdrantClient,
    genre: str | None = None,
    min_episodes: int | None = None,
    max_episodes: int | None = None,
    format: str | None = None,
    limit: int = 50,
) -> tuple[list[dict], int]:
    # Hardcoded to anilist: filter_media never had a source filter in the old
    # schema and only worked by accident (chat.js's anilist_id dedup happened
    # to prefer anime rows over review rows because they had lower ids).
    must = [FieldCondition(key="source", match=MatchValue(value="anilist"))]
    if genre is not None:
        must.append(FieldCondition(key="metadata.genres", match=MatchValue(value=genre)))
    if format is not None:
        must.append(FieldCondition(key="metadata.format", match=MatchValue(value=format)))
    if min_episodes is not None or max_episodes is not None:
        must.append(
            FieldCondition(
                key="metadata.episodes", range=Range(gte=min_episodes, lte=max_episodes)
            )
        )
    query_filter = Filter(must=must)

    total_count = client.count(
        collection_name=COLLECTION, count_filter=query_filter, exact=True
    ).count

    records, _ = client.scroll(
        collection_name=COLLECTION,
        scroll_filter=query_filter,
        limit=limit,
        order_by=OrderBy(key="metadata.popularity_rank", direction=Direction.ASC),
    )
    rows = [
        {
            "source_id": r.payload["source_id"],
            "title": r.payload["title"],
            "metadata": r.payload["metadata"],
        }
        for r in records
    ]
    return rows, total_count


if __name__ == "__main__":
    ensure_collection()
    print(f"Collection '{COLLECTION}' ready with payload indexes: {list(PAYLOAD_INDEXES)}")
