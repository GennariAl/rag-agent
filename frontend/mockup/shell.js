/*
 * Shared app shell for index.html and admin.html: header, user menu, toasts,
 * access-denied screen and a tiny markdown renderer.
 */
(function () {
  "use strict";

  const ADMIN_GROUP = "okd_admin";

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  /** Minimal markdown: headings, lists, blockquotes, code fences, inline styles, paragraphs. */
  function renderMarkdown(src) {
    const lines = escapeHtml(src).split("\n");
    const out = [];
    let list = null;
    let para = [];
    let code = null;
    const flushPara = () => {
      if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
      para = [];
    };
    const closeList = () => {
      if (list) out.push(`</${list}>`);
      list = null;
    };
    for (const line of lines) {
      if (code !== null) {
        if (/^```/.test(line)) {
          out.push(`<pre><code>${code.join("\n")}</code></pre>`);
          code = null;
        } else code.push(line);
        continue;
      }
      if (/^```/.test(line)) { flushPara(); closeList(); code = []; continue; }
      let m;
      if ((m = line.match(/^(#{1,4})\s+(.*)/))) { flushPara(); closeList(); out.push(`<h${m[1].length + 2}>${inline(m[2])}</h${m[1].length + 2}>`); continue; }
      if ((m = line.match(/^\s*[-*]\s+(.*)/)) || (m = line.match(/^\s*\d+\.\s+(.*)/))) {
        flushPara();
        const type = /^\s*\d+\./.test(line) ? "ol" : "ul";
        if (list !== type) { closeList(); out.push(`<${type}>`); list = type; }
        out.push(`<li>${inline(m[1])}</li>`);
        continue;
      }
      if ((m = line.match(/^&gt;\s?(.*)/))) { flushPara(); closeList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
      if (!line.trim()) { flushPara(); closeList(); continue; }
      closeList();
      para.push(line);
    }
    if (code !== null) out.push(`<pre><code>${code.join("\n")}</code></pre>`);
    flushPara();
    closeList();
    return out.join("");
  }

  function toast(message, kind = "info") {
    let host = document.getElementById("toasts");
    if (!host) {
      host = document.createElement("div");
      host.id = "toasts";
      host.className = "toasts";
      document.body.appendChild(host);
    }
    const el = document.createElement("div");
    el.className = `toast toast-${kind}`;
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => el.classList.add("leaving"), 3200);
    setTimeout(() => el.remove(), 3600);
  }

  const isAdmin = (user) => user.groups.includes(ADMIN_GROUP);
  const initials = (user) => (user.name[0] + user.surname[0]).toUpperCase();

  /** Keep mock query params (role, auth) when navigating between pages. */
  function withParams(href) {
    return href + location.search;
  }

  function renderHeader(user, active) {
    const header = document.getElementById("app-header");
    const role = isAdmin(user) ? "Admin" : "Editor";
    header.innerHTML = `
      <div class="header-left">
        <button class="icon-btn sidebar-toggle" id="sidebar-toggle" aria-label="Toggle sidebar" title="Toggle sidebar">☰</button>
        <a class="brand" href="${withParams("index.html")}"><span class="brand-logo">◆</span> Knowledge Assistant</a>
      </div>
      <nav class="header-right">
        <a class="nav-link ${active === "chat" ? "active" : ""}" href="${withParams("index.html")}">Chat</a>
        ${isAdmin(user) ? `<a class="nav-link ${active === "admin" ? "active" : ""}" href="${withParams("admin.html")}">Admin</a>` : ""}
        <div class="user-menu">
          <button class="avatar" id="avatar-btn" aria-haspopup="true" aria-expanded="false" title="${escapeHtml(user.name)} ${escapeHtml(user.surname)}">${initials(user)}</button>
          <div class="user-dropdown" id="user-dropdown" hidden>
            <div class="user-card">
              <div class="avatar avatar-lg">${initials(user)}</div>
              <div>
                <div class="user-name">${escapeHtml(user.name)} ${escapeHtml(user.surname)}</div>
                <div class="user-email">${escapeHtml(user.email)}</div>
                <span class="badge ${isAdmin(user) ? "badge-admin" : ""}">${role}</span>
              </div>
            </div>
            <button class="menu-item" id="signout-btn">Sign out</button>
          </div>
        </div>
      </nav>`;
    if (active !== "chat") header.querySelector("#sidebar-toggle").remove();
    const btn = header.querySelector("#avatar-btn");
    const dd = header.querySelector("#user-dropdown");
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      dd.hidden = !dd.hidden;
      btn.setAttribute("aria-expanded", String(!dd.hidden));
    });
    document.addEventListener("click", (e) => { if (!dd.contains(e.target)) dd.hidden = true; });
    header.querySelector("#signout-btn").addEventListener("click", () => toast("Signed out (mock) — the real app redirects to the identity provider."));
  }

  function renderAccessDenied(err) {
    const status = err.status || 403;
    document.body.innerHTML = `
      <main class="denied">
        <div class="denied-card">
          <div class="denied-icon">🔒</div>
          <h1>${status === 401 ? "Session expired" : "Access denied"}</h1>
          <p>${escapeHtml(err.problem ? err.problem.detail : err.message)}</p>
          <p class="muted">Error ${status}${err.problem && err.problem.instance ? " · " + escapeHtml(err.problem.instance) : ""}</p>
          ${status === 401 ? '<button class="btn btn-primary" onclick="location.reload()">Sign in again</button>' : '<p class="muted">Contact your administrator to request access.</p>'}
        </div>
      </main>`;
  }

  async function bootUser(active) {
    try {
      const user = await window.MockApi.getUserInfo();
      renderHeader(user, active);
      return user;
    } catch (err) {
      renderAccessDenied(err);
      return null;
    }
  }

  window.Shell = { escapeHtml, renderMarkdown, toast, isAdmin, bootUser, renderAccessDenied, withParams };
})();
