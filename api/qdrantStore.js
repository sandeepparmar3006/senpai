// Shared Qdrant helpers for the media_chunks vector store, used by chat.js.
// Mirrors ingest/qdrant_store.py (same collection, same param names/order)
// so the two don't drift silently -- this file is read-only (search/filter),
// writes only ever happen from the Python ingest side.
import { QdrantClient } from "@qdrant/js-client-rest";

export const COLLECTION = "media_chunks";

let client;
export function getClient() {
  if (!client) {
    client = new QdrantClient({
      url: process.env.QDRANT_URL,
      apiKey: process.env.QDRANT_API_KEY,
    });
  }
  return client;
}

export async function search(qdrant, queryEmbedding, k = 5, sourceFilter = null) {
  const filter = sourceFilter
    ? { must: [{ key: "source", match: { value: sourceFilter } }] }
    : undefined;
  const result = await qdrant.search(COLLECTION, {
    vector: queryEmbedding,
    limit: k,
    filter,
    with_payload: true,
  });
  return result.map((hit) => ({
    id: hit.id,
    source_id: hit.payload.source_id,
    title: hit.payload.title,
    chunk_text: hit.payload.chunk_text,
    metadata: hit.payload.metadata,
    similarity: hit.score,
  }));
}

// Hardcoded to anilist: filter_media never had a source filter in the old
// schema and only worked by accident (filterLookup's anilist_id dedup
// happened to prefer anime rows over review rows, which have higher ids).
export async function filterQuery(qdrant, { genre, minEpisodes, maxEpisodes, format, limit = 50 } = {}) {
  const must = [{ key: "source", match: { value: "anilist" } }];
  if (genre != null) must.push({ key: "metadata.genres", match: { value: genre } });
  if (format != null) must.push({ key: "metadata.format", match: { value: format } });
  if (minEpisodes != null || maxEpisodes != null) {
    must.push({
      key: "metadata.episodes",
      range: { gte: minEpisodes ?? undefined, lte: maxEpisodes ?? undefined },
    });
  }
  const filter = { must };

  const [{ count: totalCount }, { points }] = await Promise.all([
    qdrant.count(COLLECTION, { filter, exact: true }),
    qdrant.scroll(COLLECTION, {
      filter,
      limit,
      order_by: { key: "metadata.popularity_rank", direction: "asc" },
      with_payload: true,
    }),
  ]);

  // total_count attached to every row -- mirrors the old Postgres RPC's
  // `count(*) over ()` shape so chat.js's downstream reads (results[0]?.total_count)
  // don't need to change.
  return points.map((r) => ({
    source_id: r.payload.source_id,
    title: r.payload.title,
    metadata: r.payload.metadata,
    total_count: totalCount,
  }));
}
