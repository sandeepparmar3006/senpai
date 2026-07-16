import { createClient } from "@supabase/supabase-js";
import { Readable } from "node:stream";

const TOGETHER_API_KEY = process.env.TOGETHER_API_KEY;
const EMBED_MODEL = "intfloat/multilingual-e5-large-instruct";
const CHAT_MODEL = "openai/gpt-oss-20b";
const K = 5;

const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);

// Spend/abuse guard: public endpoint calls a paid LLM per request.
// Per-IP cap deters casual abuse; the global daily ceiling bounds worst-case
// daily spend even under distributed abuse. Fail-open on limiter errors so a
// rate_limits outage never takes the whole app down.
const IP_LIMIT = { max: 15, windowSeconds: 60 };
const GLOBAL_LIMIT = { max: 1000, windowSeconds: 86400 };

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

async function route(question) {
  const data = await chatCompletion({
    messages: [
      {
        role: "system",
        content:
          "Decide how to answer the user's anime/manga question by calling exactly one tool. " +
          "First check: does the question ask for an opinion, recommendation, rating, or reception about a specific named anime — is it good, is it worth watching, how is the pacing, what do people think, should I watch it? If so, always choose opinion_search, even if it also mentions plot or characters in passing. " +
          "Otherwise, if the question names a specific anime and asks about its plot, characters, or details, choose semantic_search, even if phrased as 'what X'. " +
          "Only choose filter_lookup when the question asks to list, count, or filter across multiple anime by genre, episode count, or format.",
      },
      { role: "user", content: question },
    ],
    tools: TOOLS,
    tool_choice: "required",
  });
  // Open-weight models sometimes emit multiple/redundant tool_calls; the first is authoritative.
  return data.choices[0].message.tool_calls?.[0] ?? null;
}

async function embed(text) {
  const resp = await fetch("https://api.together.xyz/v1/embeddings", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOGETHER_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ model: EMBED_MODEL, input: text }),
  });
  const data = await resp.json();
  return data.data[0].embedding;
}

async function semanticSearch(searchQuery, sourceFilter = null) {
  const embedding = await embed(searchQuery);
  const { data, error } = await supabase.rpc("match_media_chunks", {
    query_embedding: embedding,
    match_count: K,
    source_filter: sourceFilter,
  });
  if (error) throw error;
  return data;
}

async function filterLookup(args) {
  const { data, error } = await supabase.rpc("filter_media", {
    genre_filter: args.genre ?? null,
    min_episodes: args.min_episodes ?? null,
    max_episodes: args.max_episodes ?? null,
    format_filter: args.format ?? null,
  });
  if (error) throw error;
  
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

function buildContext(routeName, results) {
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

async function streamGenerate(res, question, routeName, results) {
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
            routeName === "opinion_search"
              ? OPINION_SYSTEM_PROMPT
              : "Answer only using the provided context. Cite anime titles in brackets.",
        },
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
  const { query } = req.body;
  if (!query) {
    res.status(400).json({ error: "query required" });
    return;
  }

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
    toolCall = await route(query);
    const calledName = toolCall?.function?.name;
    routeName = calledName === "filter_lookup" || calledName === "opinion_search" ? calledName : "semantic_search";
    routeArgs = toolCall?.function?.arguments ? JSON.parse(toolCall.function.arguments) : {};
    if (routeName === "filter_lookup") {
      results = await filterLookup(routeArgs);
    } else if (routeName === "opinion_search") {
      results = await semanticSearch(routeArgs.query || query, "jikan_review");
    } else {
      results = await semanticSearch(routeArgs.query || query);
    }
  } catch (err) {
    res.status(502).json({ error: "Lookup failed. Try again in a moment." });
    return;
  }

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
  });

  const detail =
    routeName === "filter_lookup"
      ? { genre: routeArgs.genre ?? null, min_episodes: routeArgs.min_episodes ?? null, max_episodes: routeArgs.max_episodes ?? null, format: routeArgs.format ?? null }
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
    if (routeName === "opinion_search") {
      // source_id is a review id (mal-review composite); anilist_id in metadata is what the
      // client needs to fetch cover art, so it's surfaced as source_id here instead.
      return { title: r.title, source_id: r.metadata?.anilist_id ?? null, similarity: r.similarity, score: r.metadata?.score ?? null };
    }
    return { title: r.title, source_id: r.metadata?.anilist_id ?? r.source_id, similarity: r.similarity };
  });
  res.write(`event: meta\ndata: ${JSON.stringify({ route: routeName, detail, sources })}\n\n`);

  try {
    await streamGenerate(res, query, routeName, results);
  } catch (err) {
    res.write(`event: error\ndata: ${JSON.stringify({ message: "Stream interrupted. Partial answer shown." })}\n\n`);
  }

  res.write(`event: done\ndata: {}\n\n`);
  res.end();
}
