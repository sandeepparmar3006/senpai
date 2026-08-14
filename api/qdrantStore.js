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

export const SPARSE_NAME = "sparse";
const RRF_K = 60;

// MUST stay byte-identical with tokenize() in ingest/qdrant_store.py --
// ingest-time and query-time sparse vectors are computed in different languages.
// BMP-only range on purpose: JS regexes are UTF-16 code units.
const TOKEN_SPLIT = /[^a-z0-9-￿]+/;

export function tokenize(text) {
  return text
    .normalize("NFKC")
    .toLowerCase()
    .split(TOKEN_SPLIT)
    .filter((t) => t.length >= 2);
}

function fnv1a32(token) {
  let h = 0x811c9dc5;
  for (const byte of new TextEncoder().encode(token)) {
    h ^= byte;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h;
}

// Term-frequency sparse vector; IDF weighting is applied server-side (Modifier.IDF).
export function sparseVector(text) {
  const counts = new Map();
  for (const t of tokenize(text)) {
    const idx = fnv1a32(t);
    counts.set(idx, (counts.get(idx) ?? 0) + 1);
  }
  const indices = [...counts.keys()].sort((a, b) => a - b);
  return { indices, values: indices.map((i) => counts.get(i)) };
}

function hitDict(hit, similarity) {
  return {
    id: hit.id,
    source_id: hit.payload.source_id,
    title: hit.payload.title,
    chunk_text: hit.payload.chunk_text,
    metadata: hit.payload.metadata,
    similarity,
  };
}

// Hybrid dense+sparse search, fused client-side with RRF. Fusion is client-side
// (not Qdrant's fusion query) so `similarity` stays a real cosine score -- the
// miss-logging threshold and UI pills depend on cosine semantics. Sparse-only
// hits get similarity null. Without queryText, falls back to dense-only.
export async function search(qdrant, queryEmbedding, k = 5, sourceFilter = null, queryText = null) {
  const filter = sourceFilter
    ? { must: [{ key: "source", match: { value: sourceFilter } }] }
    : undefined;

  const densePromise = qdrant.query(COLLECTION, {
    query: queryEmbedding,
    limit: k,
    filter,
    with_payload: true,
  });

  const sv = queryText != null ? sparseVector(queryText) : { indices: [] };
  const sparsePromise = sv.indices.length
    ? qdrant.query(COLLECTION, {
        query: sv,
        using: SPARSE_NAME,
        limit: k,
        filter,
        with_payload: true,
      })
    : Promise.resolve({ points: [] });

  const [dense, sparse] = await Promise.all([densePromise, sparsePromise]);
  if (!sparse.points.length) return dense.points.map((h) => hitDict(h, h.score));

  const fused = new Map();
  for (const [points, isDense] of [[dense.points, true], [sparse.points, false]]) {
    points.forEach((h, rank) => {
      const key = String(h.id);
      const entry = fused.get(key) ?? { hit: h, rrf: 0, cosine: null };
      entry.rrf += 1 / (RRF_K + rank + 1);
      if (isDense) entry.cosine = h.score;
      fused.set(key, entry);
    });
  }
  return [...fused.values()]
    .sort((a, b) => b.rrf - a.rrf)
    .slice(0, k)
    .map((e) => hitDict(e.hit, e.cosine));
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
