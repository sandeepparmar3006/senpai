const form = document.getElementById("chat-form");
const input = document.getElementById("query");
const messages = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const sendBtn = document.getElementById("send-btn");
const scrollLatestBtn = document.getElementById("scroll-latest");
const header = document.querySelector(".app-header");

function hideEmptyState() {
  document.body.classList.add("chat-active");
}

function isNearBottom(threshold = 120) {
  return messages.scrollHeight - messages.scrollTop - messages.clientHeight < threshold;
}

function scrollToBottom(smooth = true) {
  messages.scrollTo({ top: messages.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  scrollLatestBtn.classList.remove("visible");
}

messages.addEventListener("scroll", () => {
  header.classList.toggle("scrolled", messages.scrollTop > 4);
  const hasOverflow = messages.scrollHeight > messages.clientHeight;
  scrollLatestBtn.classList.toggle("visible", hasOverflow && !isNearBottom());
});

scrollLatestBtn.addEventListener("click", () => scrollToBottom(true));

function addBubble(role, text, sources, route) {
  const wasNearBottom = role === "user" || isNearBottom();

  const row = document.createElement("div");
  row.className = `bubble-row ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (route) {
    const badge = document.createElement("div");
    badge.className = "route-badge";
    badge.textContent =
      route === "filter_lookup" ? "structured lookup" : route === "opinion_search" ? "fan reviews" : "semantic search";
    bubble.appendChild(badge);
  }

  const textEl = document.createElement("div");
  textEl.textContent = text;
  bubble.appendChild(textEl);
  row.appendChild(bubble);

  if (sources && sources.length) {
    appendSources(row, sources, route);
  }

  messages.appendChild(row);
  if (wasNearBottom) {
    scrollToBottom(true);
  } else {
    scrollLatestBtn.classList.add("visible");
  }
  return { row, textEl };
}

const coverCache = new Map();

async function fetchCovers(ids) {
  const missing = ids.filter((id) => id != null && !coverCache.has(String(id)));
  if (missing.length) {
    try {
      const resp = await fetch("https://graphql.anilist.co", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: "query($ids:[Int]){Page(perPage:25){media(id_in:$ids){id coverImage{medium color}}}}",
          variables: { ids: missing.map(Number) },
        }),
      });
      const data = await resp.json();
      for (const m of data?.data?.Page?.media || []) {
        coverCache.set(String(m.id), m.coverImage || {});
      }
    } catch {
      /* covers are decoration; cards render without them */
    }
    for (const id of missing) if (!coverCache.has(String(id))) coverCache.set(String(id), {});
  }
  return coverCache;
}

function appendSources(row, sources, route) {
  const bubble = row.querySelector(".bubble");
  const list = document.createElement("div");
  list.className = "sources";
  const cards = sources.map((s) => {
    const card = document.createElement("div");
    card.className = "source-card";

    const thumb = document.createElement("div");
    thumb.className = "source-thumb";
    card.appendChild(thumb);

    const info = document.createElement("div");
    info.className = "source-info";
    const title = document.createElement("span");
    title.className = "source-title";
    title.textContent = s.title;
    info.appendChild(title);

    const meta = document.createElement("span");
    meta.className = "source-meta";
    if (route === "filter_lookup") {
      meta.textContent = `${s.episodes ?? "-"} eps · ${s.format ?? "-"}`;
    } else if (route === "opinion_search" && s.score != null) {
      const label = document.createElement("span");
      label.textContent = `${s.score}/10`;
      meta.appendChild(label);
    } else if (s.similarity != null) {
      const pct = Math.round(s.similarity * 100);
      const meter = document.createElement("span");
      meter.className = "meter";
      meter.setAttribute("aria-hidden", "true");
      const fill = document.createElement("span");
      fill.style.width = `${pct}%`;
      meter.appendChild(fill);
      const label = document.createElement("span");
      label.textContent = `${pct}% match`;
      meta.append(meter, label);
    }
    info.appendChild(meta);
    card.appendChild(info);
    list.appendChild(card);
    return { card, thumb, id: s.source_id };
  });
  bubble.appendChild(list);

  fetchCovers(cards.map((c) => c.id)).then((cache) => {
    for (const { thumb, id } of cards) {
      const cover = cache.get(String(id));
      if (cover?.medium) {
        const img = document.createElement("img");
        img.src = cover.medium;
        img.alt = "";
        img.loading = "lazy";
        thumb.appendChild(img);
        thumb.classList.add("loaded");
      }
    }
  });
}

function describeRoute(meta) {
  if (meta.route === "filter_lookup") {
    const d = meta.detail || {};
    const parts = [];
    if (d.genre) parts.push(`genre: ${d.genre}`);
    if (d.min_episodes != null) parts.push(`min episodes: ${d.min_episodes}`);
    if (d.max_episodes != null) parts.push(`max episodes: ${d.max_episodes}`);
    if (d.format) parts.push(`format: ${d.format}`);
    return `Filtered the full corpus by ${parts.join(", ") || "no criteria"}.`;
  }
  if (meta.route === "opinion_search") {
    return `Embedded the question and searched fan reviews for: "${meta.detail?.searchQuery ?? ""}".`;
  }
  return `Embedded the question and ran a cosine-similarity search for: "${meta.detail?.searchQuery ?? ""}".`;
}

function appendHowItWorks(row, meta) {
  const bubble = row.querySelector(".bubble");
  const details = document.createElement("details");
  details.className = "how-it-works";

  const summary = document.createElement("summary");
  summary.textContent = "How this was found";
  details.appendChild(summary);

  const body = document.createElement("div");
  body.className = "how-body";

  const desc = document.createElement("p");
  desc.className = "how-desc";
  desc.textContent = describeRoute(meta);
  body.appendChild(desc);

  details.appendChild(body);
  bubble.appendChild(details);
}

function addTypingIndicator() {
  const wasNearBottom = isNearBottom();
  const row = document.createElement("div");
  row.className = "bubble-row assistant";
  row.id = "typing-row";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = '<div class="skeleton-lines"><div class="skeleton-line"></div><div class="skeleton-line"></div><div class="skeleton-line"></div></div>';
  row.appendChild(bubble);
  messages.appendChild(row);
  if (wasNearBottom) scrollToBottom(true);
}

function removeTypingIndicator() {
  document.getElementById("typing-row")?.remove();
}

async function ask(query) {
  hideEmptyState();
  addBubble("user", query);
  sendBtn.disabled = true;
  addTypingIndicator();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });

    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      removeTypingIndicator();
      addBubble("error", data.error || "Something went wrong reaching SenpAI. Try again in a moment.");
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let meta = { route: null, sources: [] };
    let answer = "";
    let assistant = null;
    let autoScroll = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop();

      for (const evt of events) {
        let eventName = "message";
        let dataStr = "";
        for (const line of evt.split("\n")) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
        }
        if (!dataStr) continue;
        const data = JSON.parse(dataStr);

        if (eventName === "meta") {
          meta = data;
        } else if (eventName === "token") {
          if (!assistant) {
            removeTypingIndicator();
            autoScroll = isNearBottom();
            assistant = addBubble("assistant", "", null, meta.route);
            const cursor = document.createElement("span");
            cursor.className = "stream-cursor";
            assistant.textEl.appendChild(cursor);
          }
          const chunkSpan = document.createElement("span");
          chunkSpan.className = "token-chunk";
          chunkSpan.textContent = data.text;
          const cursorEl = assistant.textEl.querySelector(".stream-cursor");
          if (cursorEl) {
            assistant.textEl.insertBefore(chunkSpan, cursorEl);
          } else {
            assistant.textEl.appendChild(chunkSpan);
          }
          if (autoScroll) scrollToBottom(false);
        } else if (eventName === "error") {
          removeTypingIndicator();
          addBubble("error", data.message);
        } else if (eventName === "done") {
          if (assistant) {
            assistant.row.querySelector(".stream-cursor")?.remove();
            if (meta.sources?.length) {
              appendSources(assistant.row, meta.sources, meta.route);
              appendHowItWorks(assistant.row, meta);
            }
          }
        }
      }
    }
  } catch (err) {
    removeTypingIndicator();
    addBubble("error", "Something went wrong reaching SenpAI. Try again in a moment.");
  } finally {
    sendBtn.disabled = false;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const query = input.value.trim();
  if (!query) return;
  input.value = "";
  ask(query);
});

document.querySelectorAll(".suggestion-card").forEach((card) => {
  card.addEventListener("click", () => ask(card.dataset.query));
});
