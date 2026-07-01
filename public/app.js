const form = document.getElementById("chat-form");
const input = document.getElementById("query");
const messages = document.getElementById("messages");

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `message ${role}`;
  div.textContent = text;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = input.value.trim();
  if (!query) return;
  addMessage("user", query);
  input.value = "";

  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  const data = await resp.json();
  if (data.error) {
    addMessage("error", data.error);
    return;
  }
  const sources = data.sources.map((s) => s.title).join(", ");
  addMessage("assistant", `${data.answer}\n\nSources: ${sources}`);
});
