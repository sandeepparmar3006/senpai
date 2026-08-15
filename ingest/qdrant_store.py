"""Shared Qdrant helpers for the media_chunks vector store.

Used by the ingest load path and eval/eval.py, so retrieval logic (K, filter
semantics, point-ID scheme) lives in one place instead of drifting between
callers -- mirrored on the JS side by api/qdrantStore.js.
"""
import os
import re
import unicodedata
import uuid
from collections import Counter

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Direction,
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    Modifier,
    OrderBy,
    PayloadSchemaType,
    PointStruct,
    Range,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

load_dotenv()

# .get() so the module imports without credentials (e.g. in CI); network calls still require them
QDRANT_URL = os.environ.get("QDRANT_URL")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
COLLECTION = "media_chunks"
VECTOR_SIZE = 1024
SPARSE_NAME = "sparse"
# Reciprocal-rank-fusion constant (standard value from the RRF paper).
RRF_K = 60

# BMP-only on purpose: JS regexes are UTF-16 code units, so sticking to
# \\u0080-\\uffff keeps the two implementations identical (astral chars split).
_TOKEN_SPLIT = re.compile("[^a-z0-9\\u0080-\\uffff]+")


def tokenize(text: str) -> list[str]:
    # MUST stay byte-identical with tokenize() in api/qdrantStore.js -- ingest-time
    # and query-time sparse vectors are computed in different languages.
    text = unicodedata.normalize("NFKC", text).lower()
    return [t for t in _TOKEN_SPLIT.split(text) if len(t) >= 2]


def _fnv1a32(token: str) -> int:
    h = 0x811C9DC5
    for byte in token.encode("utf-8"):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


def sparse_vector(text: str) -> SparseVector:
    """Term-frequency sparse vector; IDF weighting is applied server-side (Modifier.IDF)."""
    counts = Counter(_fnv1a32(t) for t in tokenize(text))
    indices = sorted(counts)
    return SparseVector(indices=indices, values=[float(counts[i]) for i in indices])

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
    sparse_config = {SPARSE_NAME: SparseVectorParams(modifier=Modifier.IDF)}
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            sparse_vectors_config=sparse_config,
        )
    info = client.get_collection(COLLECTION)
    if not info.config.params.sparse_vectors:
        client.update_collection(collection_name=COLLECTION, sparse_vectors_config=sparse_config)
    existing = info.payload_schema
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
            vector={"": c["embedding"], SPARSE_NAME: sparse_vector(c["chunk_text"])},
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


def _hit_dict(h, similarity) -> dict:
    return {
        "id": h.id,
        "source_id": h.payload["source_id"],
        "title": h.payload["title"],
        "chunk_text": h.payload["chunk_text"],
        "metadata": h.payload["metadata"],
        "similarity": similarity,
    }


def search(
    client: QdrantClient,
    query_embedding: list[float],
    k: int = 5,
    source_filter: str | None = None,
    query_text: str | None = None,
) -> list[dict]:
    """Hybrid dense+sparse search, fused client-side with RRF.

    Fusion happens client-side (not Qdrant's fusion query) so `similarity`
    stays a real cosine score -- the miss-logging threshold and UI pills
    depend on cosine semantics. Sparse-only hits get similarity None.
    Without query_text, falls back to dense-only (pre-hybrid behaviour).
    """
    query_filter = None
    if source_filter is not None:
        query_filter = Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=source_filter))]
        )
    dense = client.query_points(
        collection_name=COLLECTION,
        query=query_embedding,
        limit=k,
        query_filter=query_filter,
    ).points

    sparse = []
    if query_text is not None:
        sv = sparse_vector(query_text)
        if sv.indices:
            sparse = client.query_points(
                collection_name=COLLECTION,
                query=sv,
                using=SPARSE_NAME,
                limit=k,
                query_filter=query_filter,
            ).points
    if not sparse:
        return [_hit_dict(h, h.score) for h in dense]

    fused: dict = {}
    for hits, is_dense in ((dense, True), (sparse, False)):
        for rank, h in enumerate(hits):
            entry = fused.setdefault(str(h.id), {"hit": h, "rrf": 0.0, "cosine": None})
            entry["rrf"] += 1.0 / (RRF_K + rank + 1)
            if is_dense:
                entry["cosine"] = h.score
    ordered = sorted(fused.values(), key=lambda e: e["rrf"], reverse=True)[:k]
    return [_hit_dict(e["hit"], e["cosine"]) for e in ordered]


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


# Structured facts (episodes, format) only live reliably on an entry's MAIN
# chunk -- cast/lore chunks carry a narrower metadata dict without them (see
# ingest/chunk_and_embed.py). The main chunk always has the lowest
# popularity_rank within its entry (offset +0 vs. cast +1, lore +2, ...), so
# this is a cheap, index-backed way to get the authoritative chunk once an
# anilist_id has been resolved via semantic search -- no new payload index
# needed, same pattern as filter_query above.
def get_main_chunk_by_anilist_id(client: QdrantClient, anilist_id: int) -> dict | None:
    query_filter = Filter(
        must=[
            FieldCondition(key="source", match=MatchValue(value="anilist")),
            FieldCondition(key="metadata.anilist_id", match=MatchValue(value=anilist_id)),
        ]
    )
    records, _ = client.scroll(
        collection_name=COLLECTION,
        scroll_filter=query_filter,
        limit=1,
        order_by=OrderBy(key="metadata.popularity_rank", direction=Direction.ASC),
    )
    if not records:
        return None
    r = records[0]
    return {"source_id": r.payload["source_id"], "title": r.payload["title"], "metadata": r.payload["metadata"]}


if __name__ == "__main__":
    ensure_collection()
    print(f"Collection '{COLLECTION}' ready with payload indexes: {list(PAYLOAD_INDEXES)}")
