import { createClient } from "@supabase/supabase-js";

const TOGETHER_API_KEY = process.env.TOGETHER_API_KEY;
const EMBED_MODEL = "intfloat/multilingual-e5-large-instruct";
const CHAT_MODEL = "openai/gpt-oss-20b";
const K = 5;

const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);

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

async function retrieve(embedding) {
  const { data, error } = await supabase.rpc("match_media_chunks", {
    query_embedding: embedding,
    match_count: K,
  });
  if (error) throw error;
  return data;
}

async function generate(question, chunks) {
  const context = chunks.map((c) => `[${c.title}] ${c.chunk_text}`).join("\n\n");
  const resp = await fetch("https://api.together.xyz/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${TOGETHER_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: CHAT_MODEL,
      messages: [
        {
          role: "system",
          content: "Answer only using the provided context. Cite the anime title in brackets.",
        },
        { role: "user", content: `Context:\n${context}\n\nQuestion: ${question}` },
      ],
    }),
  });
  const data = await resp.json();
  return data.choices[0].message.content;
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

  const embedding = await embed(query);
  const chunks = await retrieve(embedding);
  const answer = await generate(query, chunks);

  res.status(200).json({
    answer,
    sources: chunks.map((c) => ({ title: c.title, source_id: c.source_id })),
  });
}
