/* ==========================================================================
   QuantumMind — AI + Quantum Chatbot frontend (Built by Muhammad Hozafa Abbasi)
   Chats are now stored server-side per account (not localStorage), so each
   user's history is private and persists across devices/browsers.
   ========================================================================== */

const chatWindow = document.getElementById("chat-window");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const statusPill = document.getElementById("status-pill");
const historyList = document.getElementById("history-list");
const newChatBtn = document.getElementById("new-chat-btn");
const sidebar = document.getElementById("sidebar");
const sidebarToggle = document.getElementById("sidebar-toggle");

const WELCOME_HTML = `👋 Welcome to <strong>QuantumMind</strong> — built by Muhammad Hozafa Abbasi.<br><br>
Describe a problem in plain language — route optimization, grouping data,
random selection, or search — and I'll route it through the cloud AI +
Quantum pipeline for you.`;

let chats = [];        // [{id, title}]
let activeChatId = null;

/* ---------------------- Backend session helpers ---------------------- */

async function fetchChats() {
  const res = await fetch("/api/sessions");
  if (!res.ok) return [];
  return res.json();
}

async function createChat() {
  const res = await fetch("/api/sessions", { method: "POST" });
  return res.json(); // {id, title}
}

async function fetchChatMessages(id) {
  const res = await fetch(`/api/sessions/${id}`);
  if (!res.ok) return null;
  return res.json(); // {id, title, messages: [{role, html, tag}]}
}

async function renameChatOnServer(id, title) {
  const res = await fetch(`/api/sessions/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return res.ok;
}

async function deleteChatOnServer(id) {
  const res = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
  return res.ok;
}

/* ---------------------- Sidebar ---------------------- */

async function startNewChat() {
  const created = await createChat();
  chats.unshift(created);
  activeChatId = created.id;
  renderSidebar();
  chatWindow.innerHTML = "";
  renderMessage("bot", WELCOME_HTML, { persist: false });
}

async function switchChat(id) {
  activeChatId = id;
  renderSidebar();
  const data = await fetchChatMessages(id);
  chatWindow.innerHTML = "";
  if (!data) return;
  if (data.messages.length === 0) {
    renderMessage("bot", WELCOME_HTML, { persist: false });
  } else {
    data.messages.forEach((m) => renderMessage(m.role, m.html, { tag: m.tag, persist: false }));
  }
  if (window.innerWidth <= 760 && sidebar) sidebar.classList.add("collapsed");
}

function renderSidebar() {
  if (!historyList) return;
  historyList.innerHTML = "";
  chats.forEach((c) => {
    const item = document.createElement("div");
    item.className = "history-item" + (c.id === activeChatId ? " active" : "");
    item.style.display = "flex";
    item.style.alignItems = "center";

    const label = document.createElement("span");
    label.textContent = c.title || "New chat";
    label.style.flex = "1";
    label.style.overflow = "hidden";
    label.style.textOverflow = "ellipsis";
    item.appendChild(label);

    const actions = document.createElement("span");
    actions.style.display = "flex";
    actions.style.gap = "4px";
    actions.style.marginLeft = "6px";

    const renameBtn = document.createElement("button");
    renameBtn.textContent = "✎";
    renameBtn.title = "Rename";
    renameBtn.type = "button";
    renameBtn.style.cssText = "background:none;border:none;color:var(--muted);cursor:pointer;font-size:12px;";
    renameBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const newTitle = prompt("Rename chat:", c.title || "New chat");
      if (newTitle && newTitle.trim()) {
        const ok = await renameChatOnServer(c.id, newTitle.trim());
        if (ok) { c.title = newTitle.trim().slice(0, 120); renderSidebar(); }
      }
    });

    const delBtn = document.createElement("button");
    delBtn.textContent = "🗑";
    delBtn.title = "Delete";
    delBtn.type = "button";
    delBtn.style.cssText = "background:none;border:none;color:var(--danger);cursor:pointer;font-size:12px;";
    delBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete "${c.title || 'this chat'}"? This can't be undone.`)) return;
      const ok = await deleteChatOnServer(c.id);
      if (ok) {
        chats = chats.filter((x) => x.id !== c.id);
        if (activeChatId === c.id) {
          if (chats.length > 0) switchChat(chats[0].id);
          else startNewChat();
        }
        renderSidebar();
      }
    });

    actions.appendChild(renameBtn);
    actions.appendChild(delBtn);
    item.appendChild(actions);

    item.addEventListener("click", () => switchChat(c.id));
    historyList.appendChild(item);
  });
}

/* ---------------------- Rendering ---------------------- */

function renderMarkdown(text) {
  try {
    return marked.parse(text, { breaks: true });
  } catch {
    return text;
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function renderMessage(sender, html, opts = {}) {
  if (!chatWindow) return null;
  const wrap = document.createElement("div");
  wrap.className = `message ${sender}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar " + (sender === "bot" ? "bot-avatar" : "user-avatar");
  avatar.textContent = sender === "bot" ? "Q" : "U";

  const col = document.createElement("div");
  col.className = "bubble-col";

  const bubble = document.createElement("div");
  bubble.className = "bubble" + (opts.thinking ? " thinking-bubble" : "") + (opts.error ? " error-bubble" : "");

  if (opts.thinking) {
    bubble.innerHTML = `<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>`;
  } else {
    bubble.innerHTML = html;
  }

  col.appendChild(bubble);

  if (opts.tag) {
    const tag = document.createElement("div");
    tag.className = "meta-tag";
    tag.textContent = opts.tag;
    col.appendChild(tag);
  }

  if (!opts.thinking) {
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.type = "button";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", () => {
      const plain = bubble.innerText;
      navigator.clipboard.writeText(plain).then(() => {
        copyBtn.textContent = "Copied ✓";
        setTimeout(() => (copyBtn.textContent = "Copy"), 1200);
      });
    });
    actions.appendChild(copyBtn);
    col.appendChild(actions);
  }

  wrap.appendChild(avatar);
  wrap.appendChild(col);
  chatWindow.appendChild(wrap);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return wrap;
}

/* ---------------------- Health check ---------------------- */

async function checkHealth() {
  if (!statusPill) return;
  try {
    const res = await fetch("/health");
    const data = await res.json();
    if (data.llm_configured) {
      statusPill.textContent = data.ibm_quantum_configured ? "IBM Quantum · online" : "local sim · online";
      statusPill.classList.add("online");
    } else {
      statusPill.textContent = "AI key missing";
      statusPill.classList.add("error");
    }
  } catch {
    statusPill.textContent = "offline";
    statusPill.classList.add("error");
  }
}

/* ---------------------- Sending messages ---------------------- */

async function sendMessage(message) {
  renderMessage("user", escapeHtml(message));
  const thinkingEl = renderMessage("bot", "", { thinking: true });

  if (sendBtn) sendBtn.disabled = true;
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: activeChatId }),
    });
    const data = await res.json();
    if (thinkingEl) thinkingEl.remove();

    if (!res.ok) {
      renderMessage("bot", escapeHtml(data.error || "Something went wrong."), { error: true });
      return;
    }

    if (data.session_id && data.session_id !== activeChatId) {
      activeChatId = data.session_id;
    }
    // Refresh the sidebar title (backend derives it from the first message).
    const freshChats = await fetchChats();
    chats = freshChats;
    renderSidebar();

    renderMessage("bot", renderMarkdown(data.reply), { tag: data.tag });
  } catch (err) {
    if (thinkingEl) thinkingEl.remove();
    renderMessage("bot", "Network error — is the server running?", { error: true });
  } finally {
    if (sendBtn) sendBtn.disabled = false;
  }
}

if (chatForm) {
  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!chatInput) return;
    const message = chatInput.value.trim();
    if (!message) return;
    chatInput.value = "";
    chatInput.style.height = "auto";
    sendMessage(message);
  });
}

if (chatInput) {
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (chatForm) chatForm.requestSubmit();
    }
  });

  chatInput.addEventListener("input", () => {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 140) + "px";
  });
}

/* ---------------------- Sidebar controls ---------------------- */

if (newChatBtn) newChatBtn.addEventListener("click", startNewChat);

if (sidebarToggle && sidebar) {
  sidebarToggle.addEventListener("click", () => {
    sidebar.classList.toggle("collapsed");
  });
}

/* ---------------------- About Us modal ---------------------- */

const aboutBtn = document.getElementById("about-btn");
const aboutOverlay = document.getElementById("about-overlay");
const aboutClose = document.getElementById("about-close");

function openAbout() {
  if (aboutOverlay) aboutOverlay.classList.remove("hidden");
}
function closeAbout() {
  if (aboutOverlay) aboutOverlay.classList.add("hidden");
}

if (aboutBtn) aboutBtn.addEventListener("click", openAbout);
if (aboutClose) aboutClose.addEventListener("click", closeAbout);
if (aboutOverlay) {
  aboutOverlay.addEventListener("click", (e) => {
    if (e.target === aboutOverlay) closeAbout();
  });
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAbout();
});

/* ---------------------- Broadcast popup ---------------------- */

async function checkBroadcasts() {
  try {
    const res = await fetch("/api/broadcasts/pending");
    if (!res.ok) return;
    const pending = await res.json();
    for (const b of pending) {
      await showBroadcastPopup(b);
      await fetch(`/api/broadcasts/${b.id}/ack`, { method: "POST" });
    }
  } catch {
    /* silently ignore — broadcasts are non-critical */
  }
}

function showBroadcastPopup(b) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "about-overlay";
    overlay.innerHTML = `
      <div class="about-card" style="max-width:380px;">
        <div class="about-logo-dot"></div>
        <h2>📢 Announcement</h2>
        <p>${escapeHtml(b.message)}</p>
        <button type="button" class="new-chat-btn" style="width:100%; justify-content:center;">Got it</button>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector("button").addEventListener("click", () => {
      overlay.remove();
      resolve();
    });
  });
}

/* ---------------------- Boot ---------------------- */

async function boot() {
  checkHealth();
  checkBroadcasts();

  chats = await fetchChats();
  if (chats.length === 0) {
    await startNewChat();
  } else {
    renderSidebar();
    await switchChat(chats[0].id);
  }

  if (window.innerWidth <= 760 && sidebar) sidebar.classList.add("collapsed");
}

boot();
