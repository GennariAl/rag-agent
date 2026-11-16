/*
 * Chat UI for the tool-calling agent (POST /agent/chat/stream).
 *
 * The core is `reduceTurn`, a reducer over real AG-UI event shapes. Each assistant turn
 * stores its raw events; the UI state (activity trace, answer, sources, feedback id) is
 * always derived by replaying them, so the same code can later consume the real SSE stream.
 */
(function () {
  "use strict";

  const { escapeHtml, renderMarkdown, toast } = window.Shell;
  const Api = window.MockApi;
  const STORAGE_KEY = "ka-mock-chats";
  const SIDEBAR_KEY = "ka-mock-sidebar-collapsed";

  /* ------------------------------------------------------------------ */
  /* AG-UI reducer                                                       */
  /* ------------------------------------------------------------------ */

  const initialTurn = () => ({ status: "idle", segments: [], messageId: null, error: null, step: null });

  function parseToolResult(name, content) {
    if (name !== "search_knowledge_base") return { raw: content };
    try {
      return JSON.parse(content);
    } catch {
      return { count: 0, chunks: [], hint: content };
    }
  }

  function parseArgs(raw) {
    try {
      return JSON.parse(raw);
    } catch {
      return null; // still streaming
    }
  }

  function reduceTurn(state, e) {
    const s = { ...state, segments: state.segments.slice() };
    const findSeg = (kind, id) => s.segments.findIndex((x) => x.kind === kind && x.id === id);
    const patch = (i, p) => (s.segments[i] = { ...s.segments[i], ...p });

    switch (e.type) {
      case "RUN_STARTED":
        s.status = "running";
        break;
      case "STEP_STARTED":
        s.step = e.stepName;
        break;
      case "STEP_FINISHED":
        s.step = null;
        break;
      case "TEXT_MESSAGE_START":
        s.segments.push({ kind: "text", id: e.messageId, text: "", done: false });
        break;
      case "TEXT_MESSAGE_CONTENT": {
        const i = findSeg("text", e.messageId);
        if (i >= 0) patch(i, { text: s.segments[i].text + e.delta });
        break;
      }
      case "TEXT_MESSAGE_END": {
        const i = findSeg("text", e.messageId);
        if (i >= 0) patch(i, { done: true });
        break;
      }
      case "TOOL_CALL_START":
        s.segments.push({ kind: "tool", id: e.toolCallId, name: e.toolCallName, argsRaw: "", args: null, result: null, done: false });
        break;
      case "TOOL_CALL_ARGS": {
        const i = findSeg("tool", e.toolCallId);
        if (i >= 0) {
          const argsRaw = s.segments[i].argsRaw + e.delta;
          patch(i, { argsRaw, args: parseArgs(argsRaw) || s.segments[i].args });
        }
        break;
      }
      case "TOOL_CALL_END": {
        const i = findSeg("tool", e.toolCallId);
        if (i >= 0) patch(i, { args: parseArgs(s.segments[i].argsRaw) || {} });
        break;
      }
      case "TOOL_CALL_RESULT": {
        const i = findSeg("tool", e.toolCallId);
        if (i >= 0) patch(i, { result: parseToolResult(s.segments[i].name, e.content), done: true });
        break;
      }
      case "STATE_SNAPSHOT": {
        const msgs = (e.snapshot && e.snapshot.messages) || [];
        if (msgs.length) s.messageId = msgs[msgs.length - 1].id;
        break;
      }
      case "RUN_FINISHED":
        s.status = "done";
        s.step = null;
        break;
      case "RUN_ERROR":
        s.status = "error";
        s.error = e.message || "Sorry, something went wrong. Please try again.";
        s.step = null;
        break;
      // Client-only events, stored alongside the server ones.
      case "CLIENT_ABORTED":
        s.status = "stopped";
        s.step = null;
        break;
      case "CLIENT_NETWORK_ERROR":
        s.status = "error";
        s.error = "Network error — the connection to the server was lost.";
        s.step = null;
        break;
    }
    return s;
  }

  const replay = (events) => events.reduce(reduceTurn, initialTurn());

  /* ------------------------------------------------------------------ */
  /* Selectors                                                           */
  /* ------------------------------------------------------------------ */

  /**
   * A text message is interim when it is followed by a tool call, or when no tool result
   * has been received yet in the turn (the agent always calls a tool first). The answer is
   * the last non-interim text of the run.
   */
  function classify(turn) {
    let seenResult = false;
    const interim = new Set();
    turn.segments.forEach((seg, i) => {
      if (seg.kind === "tool") {
        if (seg.result) seenResult = true;
        return;
      }
      const followedByTool = turn.segments.slice(i + 1).some((x) => x.kind === "tool");
      if (followedByTool || !seenResult) interim.add(seg.id);
    });
    const answers = turn.segments.filter((x) => x.kind === "text" && !interim.has(x.id));
    return { interim, answer: answers.length ? answers[answers.length - 1] : null };
  }

  function selectSources(turn) {
    const byDoc = new Map();
    for (const seg of turn.segments) {
      if (seg.kind !== "tool" || !seg.result || !Array.isArray(seg.result.chunks)) continue;
      for (const c of seg.result.chunks) {
        const key = c.document_title;
        if (!byDoc.has(key)) byDoc.set(key, { title: c.document_title, category: c.category, pages: new Set(), permalink: c.permalink.split("#")[0], pageLinks: {} });
        const doc = byDoc.get(key);
        doc.pages.add(c.page_number);
        doc.pageLinks[c.page_number] = c.permalink;
      }
    }
    return [...byDoc.values()].map((d) => ({ ...d, pages: [...d.pages].sort((a, b) => a - b) }));
  }

  const REASON_LABEL = { chit_chat: "small talk", out_of_scope: "out of scope" };

  function selectTrace(turn, interim) {
    const entries = [];
    const tools = turn.segments.filter((x) => x.kind === "tool");
    for (const seg of turn.segments) {
      if (seg.kind === "text" && interim.has(seg.id) && seg.text.trim()) {
        entries.push({ type: "thought", text: seg.text, live: !seg.done });
      } else if (seg.kind === "tool" && seg.name === "search_knowledge_base") {
        const a = seg.args || {};
        let status = "pending";
        if (seg.result) status = seg.result.count > 0 ? "found" : "empty";
        else if (turn.status !== "running") status = "aborted";
        const isLastTool = tools[tools.length - 1] === seg;
        entries.push({ type: "search", query: a.query, category: a.category, status, count: seg.result ? seg.result.count : 0, retrying: !isLastTool || turn.status === "running" });
      } else if (seg.kind === "tool" && seg.name === "answer_without_search") {
        entries.push({ type: "direct", reason: seg.args && seg.args.reason });
      }
    }
    const searches = tools.filter((t) => t.name === "search_knowledge_base").length;
    return { entries, searches };
  }

  /* ------------------------------------------------------------------ */
  /* Chat store (localStorage — the backend keeps the history per threadId) */
  /* ------------------------------------------------------------------ */

  const store = {
    load() {
      try {
        return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
      } catch {
        return [];
      }
    },
    save(chats) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(chats));
    },
  };

  /* ------------------------------------------------------------------ */
  /* App state                                                           */
  /* ------------------------------------------------------------------ */

  const app = {
    user: null,
    chats: [],
    chat: null, // {id (= threadId = sessionId), title, updatedAt, messages:[...]}
    controller: null,
    traceOpen: {}, // assistant message id -> bool (manual override)
  };

  const $ = (sel) => document.querySelector(sel);
  const els = {};

  function newChatObject() {
    return { id: Api.uuid(), title: "New chat", updatedAt: Date.now(), messages: [] };
  }

  function persistChat() {
    if (!app.chat || !app.chat.messages.length) return;
    app.chat.updatedAt = Date.now();
    const i = app.chats.findIndex((c) => c.id === app.chat.id);
    if (i >= 0) app.chats[i] = app.chat;
    else app.chats.unshift(app.chat);
    store.save(app.chats);
    renderSidebar();
  }

  /* ------------------------------------------------------------------ */
  /* Rendering                                                           */
  /* ------------------------------------------------------------------ */

  function renderSidebar() {
    const sorted = app.chats.slice().sort((a, b) => b.updatedAt - a.updatedAt);
    els.history.innerHTML = sorted.length
      ? sorted
          .map(
            (c) => `
        <li class="history-item ${app.chat && app.chat.id === c.id ? "active" : ""}" data-id="${c.id}">
          <button class="history-open" data-action="open" title="${escapeHtml(c.title)}">
            <span class="history-title">${escapeHtml(c.title)}</span>
            <span class="history-date">${new Date(c.updatedAt).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</span>
          </button>
          <button class="icon-btn history-delete" data-action="delete" aria-label="Delete chat" title="Delete chat">🗑</button>
        </li>`
          )
          .join("")
      : '<li class="history-empty">No conversations yet</li>';
  }

  function renderEmptyState() {
    const { SUGGESTIONS } = window.MockData;
    els.messages.innerHTML = `
      <section class="empty-state">
        <h1>Hi ${escapeHtml(app.user.name)} 👋</h1>
        <p class="lead">Ask anything about internal policies, procedures, technical manuals and FAQs.
        Every answer comes with the documents it is based on.</p>
        <div class="suggestions">
          ${SUGGESTIONS.map(
            (s) => `
            <button class="suggestion" data-question="${escapeHtml(s.question)}">
              <span class="suggestion-icon">${s.icon}</span>
              <span class="suggestion-title">${escapeHtml(s.title)}</span>
              <span class="suggestion-question">${escapeHtml(s.question)}</span>
              <span class="suggestion-hint">${escapeHtml(s.hint)}</span>
            </button>`
          ).join("")}
        </div>
        <p class="mock-tip">Mockup tip: include the word <code>error</code> in a question to simulate <code>RUN_ERROR</code>,
        or <code>network</code> to simulate a network failure.</p>
      </section>`;
  }

  function renderMessages() {
    if (!app.chat || !app.chat.messages.length) return renderEmptyState();
    els.messages.innerHTML = "";
    for (const m of app.chat.messages) {
      const el = document.createElement("div");
      el.className = m.role === "user" ? "msg msg-user" : "msg msg-assistant";
      el.dataset.id = m.id;
      els.messages.appendChild(el);
      if (m.role === "user") el.innerHTML = `<div class="bubble">${escapeHtml(m.content)}</div>`;
      else renderAssistant(m, el);
    }
    scrollToBottom(true);
  }

  function traceHtml(m, turn, interim, answer) {
    const { entries, searches } = selectTrace(turn, interim);
    if (!entries.length && turn.status !== "running") return "";
    const sources = selectSources(turn);
    const live = turn.status === "running" && !answer;
    const open = app.traceOpen[m.id] ?? live;

    let summary;
    if (live) summary = `<span class="spinner"></span> ${turn.step === "tools" ? "Searching the knowledge base…" : "Thinking…"}`;
    else if (searches) summary = `🔎 Searched ${searches} time${searches > 1 ? "s" : ""} · ${sources.length} source${sources.length === 1 ? "" : "s"}`;
    else if (entries.some((x) => x.type === "direct")) summary = "💬 Answered directly";
    else summary = "Activity";

    const items = entries
      .map((x) => {
        if (x.type === "thought") return `<li class="trace-thought">${escapeHtml(x.text)}${x.live ? '<span class="caret"></span>' : ""}</li>`;
        if (x.type === "direct") return `<li class="trace-direct">💬 Answering directly${x.reason ? ` · <span class="muted">${REASON_LABEL[x.reason] || x.reason}</span>` : ""}</li>`;
        const cat = x.category ? ` · category <span class="tag">${escapeHtml(x.category)}</span>` : "";
        let result = "";
        if (x.status === "pending") result = '<div class="trace-result"><span class="spinner"></span> Searching…</div>';
        else if (x.status === "found") result = `<div class="trace-result ok">✓ ${x.count} relevant passage${x.count > 1 ? "s" : ""} found</div>`;
        else if (x.status === "empty") result = `<div class="trace-result warn">✗ No relevant content${x.retrying ? ", retrying…" : " found"}</div>`;
        else result = '<div class="trace-result muted">Interrupted</div>';
        return `<li class="trace-search">🔎 Searching <em>“${escapeHtml(x.query || "…")}”</em>${cat}${result}</li>`;
      })
      .join("");

    return `
      <div class="trace ${open ? "open" : ""} ${live ? "live" : ""}">
        <button class="trace-chip" data-action="toggle-trace" aria-expanded="${open}">
          ${summary} <span class="chev">${open ? "▾" : "▸"}</span>
        </button>
        ${open ? `<ul class="trace-list">${items || '<li class="muted">Starting…</li>'}</ul>` : ""}
      </div>`;
  }

  function sourcesHtml(turn) {
    const sources = selectSources(turn);
    if (!sources.length) return "";
    return `
      <div class="sources">
        <div class="sources-title">Sources</div>
        <div class="source-cards">
          ${sources
            .map(
              (s) => `
            <a class="source-card" href="${escapeHtml(s.permalink)}" target="_blank" rel="noopener" title="Open document">
              <span class="source-doc">📄 ${escapeHtml(s.title)}</span>
              <span class="source-meta"><span class="tag">${escapeHtml(s.category)}</span> p. ${s.pages.join(", ")} <span class="ext">↗</span></span>
            </a>`
            )
            .join("")}
        </div>
      </div>`;
  }

  function renderAssistant(m, el) {
    el = el || els.messages.querySelector(`.msg[data-id="${m.id}"]`);
    if (!el) return;
    const turn = replay(m.events);
    const { interim, answer } = classify(turn);
    const streamingAnswer = answer && !answer.done && turn.status === "running";
    const canVote = turn.status === "done" && turn.messageId && answer;

    let body = "";
    if (answer && answer.text) body = `<div class="answer markdown ${streamingAnswer ? "typing" : ""}">${renderMarkdown(answer.text)}</div>`;

    let tail = "";
    if (turn.status === "error") {
      tail = `
        <div class="error-bubble" role="alert">
          <span>⚠️ ${escapeHtml(turn.error)}</span>
          <button class="btn btn-small" data-action="retry">↻ Retry</button>
        </div>`;
    } else if (turn.status === "stopped") {
      tail = `<div class="stopped muted">⏹ Response stopped. <button class="link-btn" data-action="retry">Regenerate</button></div>`;
    }

    const actions = canVote
      ? `
      <div class="actions">
        <button class="icon-btn" data-action="copy" title="Copy answer" aria-label="Copy answer">⧉</button>
        <button class="icon-btn vote ${m.vote === "positive" ? "voted" : ""}" data-action="up" title="Good answer" aria-label="Good answer" ${m.vote ? "disabled" : ""}>👍</button>
        <button class="icon-btn vote ${m.vote === "negative" ? "voted" : ""}" data-action="down" title="Bad answer" aria-label="Bad answer" ${m.vote ? "disabled" : ""}>👎</button>
        ${m.vote ? '<span class="muted small">Thanks for your feedback</span>' : ""}
      </div>`
      : "";

    el.innerHTML = `
      <div class="assistant-avatar">◆</div>
      <div class="assistant-body">
        ${traceHtml(m, turn, interim, answer)}
        ${body}
        ${turn.status === "done" ? sourcesHtml(turn) : ""}
        ${tail}
        ${actions}
      </div>`;
  }

  function scrollToBottom(force) {
    const box = els.scroll;
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 140;
    if (force || nearBottom) box.scrollTop = box.scrollHeight;
  }

  function setRunning(running) {
    els.send.hidden = running;
    els.stop.hidden = !running;
    els.input.disabled = running;
    if (!running) els.input.focus();
  }

  /* ------------------------------------------------------------------ */
  /* Actions                                                             */
  /* ------------------------------------------------------------------ */

  function selectChat(chat) {
    if (app.controller) return toast("Wait for the current answer or press Stop.", "warn");
    app.chat = chat;
    renderSidebar();
    renderMessages();
    closeSidebarOnMobile();
  }

  function buildRunInput(chat) {
    // RunAgentInput (camelCase, as serialised by ag_ui).
    const messages = [];
    for (const m of chat.messages) {
      if (m.role === "user") messages.push({ id: m.id, role: "user", content: m.content });
      else {
        const { answer } = classify(replay(m.events));
        if (answer && answer.text) messages.push({ id: m.id, role: "assistant", content: answer.text });
      }
    }
    return { threadId: chat.id, runId: Api.uuid(), messages, state: {}, tools: [], context: [], forwardedProps: {} };
  }

  async function runTurn(assistantMsg) {
    const input = buildRunInput(app.chat);
    // The assistant message being (re)generated must not be part of its own input.
    input.messages = input.messages.filter((x) => x.id !== assistantMsg.id);
    const controller = new AbortController();
    app.controller = controller;
    delete app.traceOpen[assistantMsg.id];
    setRunning(true);
    renderAssistant(assistantMsg);
    scrollToBottom(true);

    try {
      for await (const e of Api.streamAgent(input, controller.signal)) {
        assistantMsg.events.push(e);
        renderAssistant(assistantMsg);
        scrollToBottom();
      }
    } catch (err) {
      if (err.name === "AbortError") assistantMsg.events.push({ type: "CLIENT_ABORTED" });
      else if (err instanceof TypeError) {
        assistantMsg.events.push({ type: "CLIENT_NETWORK_ERROR" });
        toast("Network error — please check your connection.", "error");
      } else {
        const detail = err.problem ? err.problem.detail : err.message;
        assistantMsg.events.push({ type: "RUN_ERROR", message: detail });
        toast(detail, "error");
      }
      renderAssistant(assistantMsg);
    } finally {
      app.controller = null;
      setRunning(false);
      persistChat();
    }
  }

  function send(text) {
    text = text.trim();
    if (!text || app.controller) return;
    if (!app.chat) app.chat = newChatObject();
    if (!app.chat.messages.length) {
      app.chat.title = text.length > 60 ? text.slice(0, 57) + "…" : text;
      els.messages.innerHTML = "";
    }
    const userMsg = { id: Api.uuid(), role: "user", content: text };
    const assistantMsg = { id: Api.uuid(), role: "assistant", events: [], vote: null };
    app.chat.messages.push(userMsg, assistantMsg);
    persistChat();

    const u = document.createElement("div");
    u.className = "msg msg-user";
    u.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
    const a = document.createElement("div");
    a.className = "msg msg-assistant";
    a.dataset.id = assistantMsg.id;
    els.messages.append(u, a);

    els.input.value = "";
    autosize();
    runTurn(assistantMsg);
  }

  function retry(assistantMsg) {
    if (app.controller) return;
    assistantMsg.events = [];
    assistantMsg.vote = null;
    runTurn(assistantMsg);
  }

  async function vote(assistantMsg, feedback, comment) {
    const turn = replay(assistantMsg.events);
    try {
      await Api.postFeedback({ sessionId: app.chat.id, messageId: turn.messageId, feedback, comment: comment || undefined });
      toast("Thanks for your feedback", "success");
    } catch (err) {
      toast(err.problem ? err.problem.detail : "Could not send feedback.", err.status === 409 ? "warn" : "error");
      if (err.status !== 409) return;
    }
    assistantMsg.vote = feedback;
    persistChat();
    renderAssistant(assistantMsg);
  }

  /* Negative feedback modal */
  let pendingDownvote = null;
  function openFeedbackModal(assistantMsg) {
    pendingDownvote = assistantMsg;
    els.modalComment.value = "";
    els.modal.hidden = false;
    els.modalComment.focus();
  }
  function closeFeedbackModal() {
    els.modal.hidden = true;
    pendingDownvote = null;
  }

  function autosize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 200) + "px";
  }

  function closeSidebarOnMobile() {
    if (window.matchMedia("(max-width: 760px)").matches) document.body.classList.add("sidebar-collapsed");
  }

  /* ------------------------------------------------------------------ */
  /* Wiring                                                              */
  /* ------------------------------------------------------------------ */

  function findAssistant(id) {
    return app.chat && app.chat.messages.find((m) => m.id === id);
  }

  function wire() {
    Object.assign(els, {
      history: $("#history"),
      messages: $("#messages"),
      scroll: $("#chat-scroll"),
      input: $("#composer-input"),
      send: $("#send-btn"),
      stop: $("#stop-btn"),
      modal: $("#feedback-modal"),
      modalComment: $("#feedback-comment"),
    });

    if (localStorage.getItem(SIDEBAR_KEY) === "1" || window.matchMedia("(max-width: 760px)").matches) document.body.classList.add("sidebar-collapsed");
    const toggle = $("#sidebar-toggle");
    if (toggle)
      toggle.addEventListener("click", () => {
        document.body.classList.toggle("sidebar-collapsed");
        localStorage.setItem(SIDEBAR_KEY, document.body.classList.contains("sidebar-collapsed") ? "1" : "0");
      });

    $("#new-chat-btn").addEventListener("click", () => selectChat(null));

    els.history.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action]");
      if (!btn) return;
      const id = btn.closest(".history-item").dataset.id;
      const chat = app.chats.find((c) => c.id === id);
      if (btn.dataset.action === "open") selectChat(chat);
      else if (btn.dataset.action === "delete") {
        if (app.controller && app.chat && app.chat.id === id) return toast("Stop the current answer before deleting this chat.", "warn");
        if (!confirm(`Delete "${chat.title}"?`)) return;
        app.chats = app.chats.filter((c) => c.id !== id);
        store.save(app.chats);
        if (app.chat && app.chat.id === id) {
          app.chat = null;
          renderMessages();
        }
        renderSidebar();
        toast("Chat deleted");
      }
    });

    els.messages.addEventListener("click", (e) => {
      const suggestion = e.target.closest(".suggestion");
      if (suggestion) return send(suggestion.dataset.question);
      const btn = e.target.closest("[data-action]");
      if (!btn) return;
      const msgEl = btn.closest(".msg-assistant");
      const m = msgEl && findAssistant(msgEl.dataset.id);
      if (!m) return;
      const turn = replay(m.events);
      switch (btn.dataset.action) {
        case "toggle-trace": {
          const { answer } = classify(turn);
          const current = app.traceOpen[m.id] ?? (turn.status === "running" && !answer);
          app.traceOpen[m.id] = !current;
          renderAssistant(m);
          break;
        }
        case "copy": {
          const { answer } = classify(turn);
          navigator.clipboard.writeText(answer.text).then(() => toast("Copied to clipboard"), () => toast("Copy failed", "error"));
          break;
        }
        case "up":
          vote(m, "positive");
          break;
        case "down":
          openFeedbackModal(m);
          break;
        case "retry":
          retry(m);
          break;
      }
    });

    els.input.addEventListener("input", autosize);
    els.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send(els.input.value);
      }
    });
    $("#composer").addEventListener("submit", (e) => {
      e.preventDefault();
      send(els.input.value);
    });
    els.stop.addEventListener("click", () => app.controller && app.controller.abort());

    $("#feedback-cancel").addEventListener("click", closeFeedbackModal);
    $("#feedback-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const m = pendingDownvote;
      const comment = els.modalComment.value.trim();
      closeFeedbackModal();
      if (m) vote(m, "negative", comment);
    });
    els.modal.addEventListener("click", (e) => { if (e.target === els.modal) closeFeedbackModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !els.modal.hidden) closeFeedbackModal(); });
  }

  async function main() {
    const user = await window.Shell.bootUser("chat");
    if (!user) return;
    app.user = user;
    app.chats = store.load();
    wire();
    renderSidebar();
    renderMessages();
    els.input.focus();
  }

  // Exposed for debugging / pointing at the real stream later.
  window.AgentUI = { reduceTurn, replay, classify, selectSources, selectTrace };
  document.addEventListener("DOMContentLoaded", main);
})();
