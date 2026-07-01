const form = document.getElementById("chat-form");
const input = document.getElementById("query");
const messages = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const sendBtn = document.getElementById("send-btn");

function hideEmptyState() {
  if (emptyState) emptyState.remove();
}

function addBubble(role, text, sources) {
  const row = document.createElement("div");
  row.className = `bubble-row ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(bubble);

  if (sources && sources.length) {
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

  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

function addTypingIndicator() {
  const row = document.createElement("div");
  row.className = "bubble-row assistant";
  row.id = "typing-row";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = '<span class="typing-indicator"><span></span><span></span><span></span></span>';
  row.appendChild(bubble);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
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
    const data = await resp.json();
    removeTypingIndicator();
    if (data.error) {
      addBubble("error", data.error);
      return;
    }
    addBubble("assistant", data.answer, data.sources);
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
