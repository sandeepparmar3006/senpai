import { createClient } from "@supabase/supabase-js";
import { Readable } from "node:stream";
import { getClient as getQdrantClient, search as qdrantSearch, filterQuery as qdrantFilterQuery, getMainChunk as qdrantGetMainChunk } from "./qdrantStore.js";

const TOGETHER_API_KEY = process.env.TOGETHER_API_KEY;
const EMBED_MODEL = "@cf/baai/bge-m3";
const CHAT_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo";
const K = 5;

const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);

// Spend/abuse guard: public endpoint calls a paid LLM per request.
// Per-IP cap deters casual abuse; the global daily ceiling bounds worst-case
// daily spend even under distributed abuse. Fail-open on limiter errors so a
// rate_limits outage never takes the whole app down.
const IP_LIMIT = { max: 15, windowSeconds: 60 };
const GLOBAL_LIMIT = { max: 1000, windowSeconds: 86400 };

// Client sends the running transcript back on each request (no server-side
// session store). Cap turns and per-message length so a malicious payload
// can't inflate Together API cost/latency through the history alone.
const MAX_HISTORY_TURNS = 6; // 3 user+assistant exchanges
const MAX_HISTORY_CHARS = 1000;

function sanitizeHistory(history) {
  if (!Array.isArray(history)) return [];
  return history
    .filter((m) => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string")
    .slice(-MAX_HISTORY_TURNS)
    .map((m) => ({ role: m.role, content: m.content.slice(0, MAX_HISTORY_CHARS) }));
}

async function withinLimit(bucket, { max, windowSeconds }) {
  const { data, error } = await supabase.rpc("check_rate_limit", {
    bucket_key: bucket,
    max_count: max,
    window_seconds: windowSeconds,
  });
  if (error) return true; // fail-open: don't let limiter downtime break chat
  return data === true;
}

const TOOLS = [
  {
    type: "function",
    function: {
      name: "semantic_search",
      description:
        "Search anime/manga by plot, themes, or synopsis content using semantic similarity. Use for ANY question about a specific named anime's story, characters, powers, or terminology — even if the question starts with 'what' or 'which'. Do not use this to filter or list across multiple anime.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "The search query" },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "filter_lookup",
      description:
        "Filter the anime database by structured criteria across ALL entries: genre, episode count range, or format. Use ONLY when the question asks to list, count, or filter across multiple anime (e.g. 'what anime have more than N episodes', 'list horror anime', 'which are movies'). Never use this for a question about one specific named anime's plot or details — use semantic_search for that.",
      parameters: {
        type: "object",
        properties: {
          genre: {
            type: "string",
            description: "A single genre to filter by. Case-sensitive — use the exact capitalization from this list.",
            enum: ["Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Horror", "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"],
          },
          exclude_genre: {
            type: "string",
            description:
              "A single genre to EXCLUDE. Set this when the question says a genre should NOT be included, is excluded, or asks for anime 'that aren't' / 'without' that genre — e.g. 'Comedy anime that aren't Romance' means genre=\"Comedy\", exclude_genre=\"Romance\". Never put the excluded genre in the genre field.",
            enum: ["Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Horror", "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"],
          },
          min_episodes: { type: "integer" },
          max_episodes: { type: "integer" },
          format: {
            type: "string",
            description:
              "Exact uppercase format code. If the question asks which entries are 'movies', set this to \"MOVIE\"; 'TV shorts' means TV_SHORT.",
            enum: ["TV", "TV_SHORT", "MOVIE", "OVA", "ONA", "SPECIAL", "MUSIC"],
          },
        },
      },
    },
  },
  {
    type: "function",
    function: {
      name: "opinion_search",
      description:
        "Search fan reviews for opinion, reception, or recommendation questions about a specific named anime — e.g. 'is X good', 'is X worth watching', 'what do people think of X', 'how is the pacing in X'. Do not use for plot/character/terminology questions (use semantic_search) or whole-corpus filters (use filter_lookup).",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "The search query" },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "compare_titles",
      description:
        "Compare the episode counts of two specific named anime/manga (e.g. 'does X have fewer episodes than Y', 'which has more episodes, X or Y'). Use ONLY when exactly two titles are named and the comparison is about episode count.",
      parameters: {
        type: "object",
        properties: {
          title_a: { type: "string", description: "First anime title" },
          title_b: { type: "string", description: "Second anime title" },
        },
        required: ["title_a", "title_b"],
      },
    },
  },
];

async function chatCompletion(body) {
  const resp = await fetch("https://api.together.xyz/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOGETHER_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ model: CHAT_MODEL, ...body }),
  });
  return resp.json();
}

async function route(question, history = []) {
  const data = await chatCompletion({
    messages: [
      {
        role: "system",
        content:
          "Decide how to answer the user's anime/manga question by calling exactly one tool. " +
          "First check: does the question ask for an opinion, recommendation, rating, or reception about a specific named anime — is it good, is it worth watching, how is the pacing, what do people think, should I watch it? If so, always choose opinion_search, even if it also mentions plot or characters in passing. " +
          "Next, if the question names exactly two anime titles and asks to compare their episode counts (e.g. 'does X have more episodes than Y', 'which has more episodes, X or Y'), choose compare_titles. " +
          "Otherwise, if the question names a specific anime and asks about its plot, characters, or details, choose semantic_search, even if phrased as 'what X'. " +
          "Only choose filter_lookup when the question asks to list, count, or filter across multiple anime by genre, episode count, or format. " +
          "Prior turns may follow — resolve pronouns and follow-up references ('it', 'that show', 'the MC', 'him') against them before picking a tool and extracting arguments.",
      },
      ...history,
      { role: "user", content: question },
    ],
    tools: TOOLS,
    tool_choice: "required",
  });
  // Open-weight models sometimes emit multiple/redundant tool_calls; the first is authoritative.
  return data.choices[0].message.tool_calls?.[0] ?? null;
}

async function embed(text) {
  const resp = await fetch(
    `https://api.cloudflare.com/client/v4/accounts/${process.env.CLOUDFLARE_ACCOUNT_ID}/ai/run/${EMBED_MODEL}`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${process.env.CLOUDFLARE_API_TOKEN}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text: [text] }),
    }
  );
  const data = await resp.json();
  if (!resp.ok || !data.result?.data?.[0]) {
    throw new Error(`Cloudflare embed failed: ${resp.status} ${JSON.stringify(data).slice(0, 300)}`);
  }
  return data.result.data[0];
}

// Sibling entries (sequels, OVAs, side stories) of the same franchise crowd out
// the top-ranked title's own chunks with near-duplicate header text. Over-fetch
// and cap how many slots other titles can take so the top-ranked title's
// deeper chunks (description, lore) still make it into context.
const NONPRIMARY_TITLE_CAP = 2;

// A spin-off/OVA's chunk text is often narrower than the canonical entry's
// (tighter description, less cast/plot breadth), which can out-score the
// canonical entry on raw cosine similarity alone even though it's the wrong
// answer. Prefer the most popular (lowest popularity_rank) as "primary"
// rather than trusting pool[0] blindly -- pool is sorted by similarity desc.
// Restricted to titles that share a prefix with pool[0] (same franchise):
// a same-similarity-band global scan once picked an unrelated but more
// popular show (e.g. a Demon Slayer query promoting "Dropkick on My Devil!"
// as primary just because it ranked more popular globally).
function normalizeTitle(t) {
  return (t || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function isSameFranchise(a, b) {
  const na = normalizeTitle(a);
  const nb = normalizeTitle(b);
  if (!na || !nb) return false;
  const maxLen = Math.min(na.length, nb.length);
  let shared = 0;
  while (shared < maxLen && na[shared] === nb[shared]) shared++;
  return shared >= Math.min(8, maxLen);
}

function pickPrimaryTitle(pool) {
  if (!pool.length) return undefined;
  const topTitle = pool[0].title;
  let best = pool[0];
  for (const chunk of pool) {
    if (!isSameFranchise(chunk.title, topTitle)) continue;
    const bestRank = best.metadata?.popularity_rank ?? Infinity;
    const rank = chunk.metadata?.popularity_rank ?? Infinity;
    if (rank < bestRank) best = chunk;
  }
  return best.title;
}

function dedupeSiblingTitles(pool, k) {
  const primaryTitle = pickPrimaryTitle(pool);
  const kept = [];
  let nonPrimaryCount = 0;
  for (const chunk of pool) {
    if (kept.length >= k) break;
    if (chunk.title === primaryTitle) {
      kept.push(chunk);
    } else if (nonPrimaryCount < NONPRIMARY_TITLE_CAP) {
      kept.push(chunk);
      nonPrimaryCount += 1;
    }
  }
  return kept;
}

async function semanticSearch(searchQuery, sourceFilter = null) {
  const embedding = await embed(searchQuery);
  const pool = await qdrantSearch(getQdrantClient(), embedding, K * 4, sourceFilter, searchQuery);
  return dedupeSiblingTitles(pool, K);
}

// resolveTitleFacts needs the ONE exact-titled entry, not a diversified top-K --
// dedupeSiblingTitles' franchise cap can drop it, and K*4=20 isn't always enough
// pool for a short/generic bare title (e.g. "Bleach") to rank inside the top-K
// fused hits at all, even though the exact entry exists in the corpus. Widen the
// pool substantially and skip dedup; titlesMatch below is the real filter.
const TITLE_LOOKUP_POOL = 50;

async function titleLookupPool(requested) {
  const embedding = await embed(requested);
  return qdrantSearch(getQdrantClient(), embedding, TITLE_LOOKUP_POOL, null, requested);
}

// A bare title-only query's top hit isn't reliably the entry's own main chunk
// (which is where episodes/format metadata live -- see chunk_and_embed.py),
// and for short/generic titles isn't always even the right entry at all.
// So: validate the top hit's title actually matches what was asked (reusing
// the same franchise-substring check as pickPrimaryTitle) before trusting it,
// then re-fetch that entry's main chunk by anilist_id for the authoritative
// episode count instead of whatever chunk type happened to rank #1.
// Exact match, not substring: "Bleach" is a substring of e.g. "BLEACH:
// Thousand-Year Blood War - The Conflict" (a single 14-episode cour), which
// would silently answer the flagship show's question with the wrong cour's
// count. Failing closed (notFound) beats a plausible-looking wrong number.
function titlesMatch(requested, hitTitle) {
  const nr = normalizeTitle(requested);
  const nh = normalizeTitle(hitTitle);
  return !!nr && nr === nh;
}

async function resolveTitleFacts(requested) {
  const hits = await titleLookupPool(requested);
  // An anime and its manga counterpart can share the exact same title string;
  // "episodes" only applies to the anime one, so prefer it among matches
  // instead of trusting whichever ranked first.
  const matches = hits.filter((h) => titlesMatch(requested, h.title));
  const hit = matches.find((h) => h.metadata?.type !== "MANGA") ?? matches[0];
  if (!hit) {
    return { requestedTitle: requested, notFound: true };
  }
  const anilistId = hit.metadata?.anilist_id;
  const main = anilistId != null ? await qdrantGetMainChunk(getQdrantClient(), anilistId) : null;
  const source = main ?? hit;
  return { requestedTitle: requested, title: source.title, metadata: source.metadata, similarity: hit.similarity };
}

async function compareTitles(titleA, titleB) {
  return Promise.all([resolveTitleFacts(titleA), resolveTitleFacts(titleB)]);
}

async function filterLookup(args) {
  const data = await qdrantFilterQuery(getQdrantClient(), {
    genre: args.genre ?? null,
    excludeGenre: args.exclude_genre ?? null,
    minEpisodes: args.min_episodes ?? null,
    maxEpisodes: args.max_episodes ?? null,
    format: args.format ?? null,
  });

  const seen = new Set();
  const deduped = [];
  for (const r of data) {
    const id = r.metadata?.anilist_id ?? r.source_id;
    if (!seen.has(id)) {
      seen.add(id);
      deduped.push(r);
    }
  }
  return deduped;
}

// Feeds corpus-growth prioritization (ingest/review_misses.py). Threshold is
// empirical, not exact: hits on titles that exist in the corpus cluster
// 0.84-0.90 similarity, genuine gaps cluster 0.80-0.83 -- a review-queue
// signal, not a hard cutoff.
const MISS_SIMILARITY_THRESHOLD = 0.83;

async function logQuery(question, routeName, results) {
  let similarity = null;
  let resultCount = results.length;
  let isMiss;
  if (routeName === "filter_lookup") {
    resultCount = results[0]?.total_count ?? results.length;
    isMiss = resultCount === 0;
  } else if (routeName === "compare_titles") {
    resultCount = results.filter((r) => !r.notFound).length;
    isMiss = results.some((r) => r.notFound || (r.similarity !== null && r.similarity < MISS_SIMILARITY_THRESHOLD));
  } else {
    similarity = results[0]?.similarity ?? null;
    isMiss = similarity !== null && similarity < MISS_SIMILARITY_THRESHOLD;
  }
  await supabase.from("query_log").insert({
    question,
    route: routeName,
    similarity,
    result_count: resultCount,
    is_miss: isMiss,
  });
}

function buildContext(routeName, results) {
  if (routeName === "compare_titles") {
    return results
      .map((r) => (r.notFound ? `[${r.requestedTitle}] Not found in database.` : `[${r.title}] episodes: ${r.metadata?.episodes ?? "unknown"}, format: ${r.metadata?.format ?? "unknown"}`))
      .join("\n");
  }
  if (routeName === "filter_lookup") {
    const total = results[0]?.total_count ?? results.length;
    const header = `Total matching entries in the database: ${total}. Showing ${results.length} below (use the total above for any "how many" question, not a count of the list shown).`;
    const rows = results
      .map((r) => `[${r.title}] genres: ${(r.metadata.genres || []).join(", ")}, episodes: ${r.metadata.episodes}, format: ${r.metadata.format}`)
      .join("\n");
    return `${header}\n${rows}`;
  }
  return results.map((c) => `[${c.title}] ${c.chunk_text}`).join("\n\n");
}

const OPINION_SYSTEM_PROMPT =
  "Answer only using the provided fan reviews. Summarize the overall reception, note disagreement between reviewers if present, and cite anime titles in brackets. Don't present one reviewer's opinion as universal consensus.";

async function streamGenerate(res, question, routeName, results, history = []) {
  const context = buildContext(routeName, results);
  const resp = await fetch("https://api.together.xyz/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOGETHER_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: CHAT_MODEL,
      stream: true,
      messages: [
        {
          role: "system",
          content:
            (routeName === "opinion_search"
              ? OPINION_SYSTEM_PROMPT
              : "Answer only using the provided context. Cite anime titles in brackets.") +
            " Prior turns may follow for conversational context — the current question's Context block above is still the only source for facts in your answer.",
        },
        ...history,
        { role: "user", content: `Context:\n${context}\n\nQuestion: ${question}` },
      ],
    }),
  });

  if (!resp.ok || !resp.body) {
    throw new Error(`Together stream request failed: ${resp.status}`);
  }

  let buffer = "";
  for await (const chunk of Readable.fromWeb(resp.body)) {
    buffer += chunk.toString("utf8");
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const payload = trimmed.slice(5).trim();
      if (payload === "[DONE]") continue;
      let parsed;
      try {
        parsed = JSON.parse(payload);
      } catch {
        continue;
      }
      const token = parsed.choices?.[0]?.delta?.content;
      if (token) {
        res.write(`event: token\ndata: ${JSON.stringify({ text: token })}\n\n`);
      }
    }
  }
}

export default async function handler(req, res) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "POST only" });
    return;
  }
  const { query, history } = req.body;
  if (!query) {
    res.status(400).json({ error: "query required" });
    return;
  }
  const safeHistory = sanitizeHistory(history);

  const ip = (req.headers["x-forwarded-for"] || "").split(",")[0].trim() || "unknown";
  const [ipOk, globalOk] = await Promise.all([
    withinLimit(`ip:${ip}:min`, IP_LIMIT),
    withinLimit("global:day", GLOBAL_LIMIT),
  ]);
  if (!ipOk || !globalOk) {
    res.status(429).json({ error: "Busy right now — give it a minute and try again." });
    return;
  }

  let toolCall, routeName, results, routeArgs;
  try {
    toolCall = await route(query, safeHistory);
    const calledName = toolCall?.function?.name;
    routeName = ["filter_lookup", "opinion_search", "compare_titles"].includes(calledName) ? calledName : "semantic_search";
    routeArgs = toolCall?.function?.arguments ? JSON.parse(toolCall.function.arguments) : {};
    if (routeName === "filter_lookup") {
      results = await filterLookup(routeArgs);
    } else if (routeName === "opinion_search") {
      results = await semanticSearch(routeArgs.query || query, "jikan_review");
    } else if (routeName === "compare_titles") {
      if (routeArgs.title_a && routeArgs.title_b) {
        results = await compareTitles(routeArgs.title_a, routeArgs.title_b);
      } else {
        // Router failed to extract two distinct titles -- fall back to the old
        // (buggy but non-crashing) path rather than error out.
        routeName = "semantic_search";
        results = await semanticSearch(query);
      }
    } else {
      results = await semanticSearch(routeArgs.query || query);
    }
  } catch (err) {
    console.error("Lookup failed:", err);
    res.status(502).json({ error: "Lookup failed. Try again in a moment." });
    return;
  }

  logQuery(query, routeName, results).catch(() => {}); // fire-and-forget: never block chat on logging

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
  });

  const detail =
    routeName === "filter_lookup"
      ? { genre: routeArgs.genre ?? null, min_episodes: routeArgs.min_episodes ?? null, max_episodes: routeArgs.max_episodes ?? null, format: routeArgs.format ?? null }
      : routeName === "compare_titles"
        ? { titleA: routeArgs.title_a ?? null, titleB: routeArgs.title_b ?? null }
        : { searchQuery: routeArgs.query || query };

  if (results.length === 0) {
    res.write(`event: meta\ndata: ${JSON.stringify({ route: routeName, detail, sources: [] })}\n\n`);
    res.write(`event: token\ndata: ${JSON.stringify({ text: "No matching anime found in the database." })}\n\n`);
    res.write(`event: done\ndata: {}\n\n`);
    res.end();
    return;
  }

  const sources = results.map((r) => {
    if (routeName === "filter_lookup") {
      return { title: r.title, source_id: r.metadata?.anilist_id ?? r.source_id, episodes: r.metadata?.episodes ?? null, format: r.metadata?.format ?? null };
    }
    if (routeName === "compare_titles") {
      return r.notFound
        ? { title: r.requestedTitle, source_id: null, notFound: true }
        : { title: r.title, source_id: r.metadata?.anilist_id ?? r.source_id, similarity: r.similarity, episodes: r.metadata?.episodes ?? null };
    }
    if (routeName === "opinion_search") {
      // source_id is a review id (mal-review composite); anilist_id in metadata is what the
      // client needs to fetch cover art, so it's surfaced as source_id here instead.
      return { title: r.title, source_id: r.metadata?.anilist_id ?? null, similarity: r.similarity, score: r.metadata?.score ?? null };
    }
    return { title: r.title, source_id: r.metadata?.anilist_id ?? r.source_id, similarity: r.similarity };
  });
  res.write(`event: meta\ndata: ${JSON.stringify({ route: routeName, detail, sources })}\n\n`);

  try {
    await streamGenerate(res, query, routeName, results, safeHistory);
  } catch (err) {
    res.write(`event: error\ndata: ${JSON.stringify({ message: "Stream interrupted. Partial answer shown." })}\n\n`);
  }

  res.write(`event: done\ndata: {}\n\n`);
  res.end();
}
