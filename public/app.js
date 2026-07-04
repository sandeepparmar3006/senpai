const form = document.getElementById("chat-form");
const input = document.getElementById("query");
const messages = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const sendBtn = document.getElementById("send-btn");
const scrollLatestBtn = document.getElementById("scroll-latest");
const header = document.querySelector(".app-header");

function hideEmptyState() {
  if (emptyState) emptyState.remove();
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
    badge.textContent = route === "filter_lookup" ? "structured lookup" : "semantic search";
    bubble.appendChild(badge);
  }

  const textEl = document.createElement("div");
  textEl.textContent = text;
  bubble.appendChild(textEl);
  row.appendChild(bubble);

  if (sources && sources.length) {
    appendSources(row, sources);
  }

  messages.appendChild(row);
  if (wasNearBottom) {
    scrollToBottom(true);
  } else {
    scrollLatestBtn.classList.add("visible");
  }
  return { row, textEl };
}

function appendSources(row, sources) {
  const bubble = row.querySelector(".bubble");
  const list = document.createElement("div");
  list.className = "sources";
  for (const s of sources) {
    const pill = document.createElement("span");
    pill.className = "source-pill";
    pill.textContent = s.title;
    list.appendChild(pill);
  }
  bubble.appendChild(list);
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

  const list = document.createElement("ul");
  list.className = "how-list";
  for (const s of meta.sources || []) {
    const item = document.createElement("li");
    const title = document.createElement("span");
    title.textContent = s.title;
    const score = document.createElement("span");
    score.className = "how-score";
    score.textContent =
      meta.route === "filter_lookup" ? `${s.episodes ?? "—"} eps · ${s.format ?? "—"}` : `${Math.round(s.similarity * 100)}% match`;
    item.append(title, score);
    list.appendChild(item);
  }
  body.appendChild(list);

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
  bubble.innerHTML = '<span class="typing-indicator"><span></span><span></span><span></span></span>';
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
            assistant.row.querySelector(".bubble").appendChild(cursor);
          }
          answer += data.text;
          assistant.textEl.textContent = answer;
          if (autoScroll) scrollToBottom(false);
        } else if (eventName === "error") {
          removeTypingIndicator();
          addBubble("error", data.message);
        } else if (eventName === "done") {
          if (assistant) {
            assistant.row.querySelector(".stream-cursor")?.remove();
            if (meta.sources?.length) {
              appendSources(assistant.row, meta.sources);
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

document.querySelectorAll(".suggestion-chip").forEach((chip) => {
  chip.addEventListener("click", () => ask(chip.textContent));
});
