/*
 * Admin Evaluation page (okd_admin only). Fully mocked: upload a testbook CSV, run it,
 * watch progress, review verdicts and download the enriched CSV.
 */
(function () {
  "use strict";

  const { escapeHtml, toast, isAdmin, renderAccessDenied } = window.Shell;
  const $ = (sel) => document.querySelector(sel);

  const state = { fileName: null, rows: [], results: [], controller: null };

  /** RFC 4180-ish CSV parser (quoted fields, escaped quotes, CRLF). */
  function parseCsv(text) {
    const rows = [];
    let row = [];
    let field = "";
    let quoted = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
        else if (c === '"') quoted = false;
        else field += c;
      } else if (c === '"') quoted = true;
      else if (c === ",") { row.push(field); field = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(field); rows.push(row); row = []; field = "";
      } else field += c;
    }
    if (field || row.length) { row.push(field); rows.push(row); }
    return rows.filter((r) => r.some((x) => x.trim()));
  }

  const csvCell = (v) => (/[",\n]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : String(v));

  function loadCsv(name, text) {
    const rows = parseCsv(text);
    if (!rows.length) return toast("The file is empty.", "error");
    const header = rows[0].map((h) => h.trim().toLowerCase());
    const qi = header.indexOf("question");
    if (qi < 0) return toast('Missing "question" column in the CSV header.', "error");
    const ei = header.indexOf("expected_answer");
    state.fileName = name;
    state.rows = rows.slice(1).map((r) => ({ question: r[qi] || "", expected_answer: ei >= 0 ? r[ei] || "" : "" })).filter((r) => r.question.trim());
    state.results = [];
    $("#file-info").hidden = false;
    $("#file-info").innerHTML = `📄 <strong>${escapeHtml(name)}</strong> · ${state.rows.length} questions`;
    $("#run-btn").disabled = !state.rows.length;
    $("#download-btn").disabled = true;
    $("#summary").innerHTML = "";
    renderResults();
  }

  function renderResults() {
    const body = $("#results-body");
    if (!state.rows.length) {
      body.innerHTML = '<tr><td colspan="4" class="muted center">No results yet — load a testbook and run it.</td></tr>';
      return;
    }
    body.innerHTML = state.rows
      .map((r, i) => {
        const res = state.results[i];
        const verdict = res ? `<span class="verdict verdict-${res.verdict.toLowerCase()}">${res.verdict}</span>` : state.controller && i === state.results.length ? '<span class="spinner"></span>' : '<span class="muted">—</span>';
        return `<tr>
          <td class="muted">${i + 1}</td>
          <td>${escapeHtml(r.question)}${r.expected_answer ? `<div class="muted small">Expected: ${escapeHtml(r.expected_answer)}</div>` : ""}</td>
          <td class="answer-cell">${res ? escapeHtml(res.answer) : ""}</td>
          <td>${verdict}</td>
        </tr>`;
      })
      .join("");
  }

  function renderSummary() {
    const counts = { PASS: 0, PARTIAL: 0, FAIL: 0 };
    state.results.forEach((r) => counts[r.verdict]++);
    const total = state.results.length || 1;
    $("#summary").innerHTML = state.results.length
      ? `<span class="verdict verdict-pass">${counts.PASS} pass</span>
         <span class="verdict verdict-partial">${counts.PARTIAL} partial</span>
         <span class="verdict verdict-fail">${counts.FAIL} fail</span>
         <span class="muted small">${Math.round((counts.PASS / total) * 100)}% pass rate</span>`
      : "";
  }

  function setProgress(done, total) {
    $("#progress").hidden = false;
    $("#progress-fill").style.width = `${(done / total) * 100}%`;
    $("#progress-label").textContent = `${done} / ${total}`;
  }

  async function run() {
    state.results = [];
    state.controller = new AbortController();
    $("#run-btn").disabled = true;
    $("#cancel-btn").hidden = false;
    $("#download-btn").disabled = true;
    setProgress(0, state.rows.length);
    renderResults();
    try {
      for (const row of state.rows) {
        state.results.push(await window.MockApi.evaluateQuestion(row, state.controller.signal));
        setProgress(state.results.length, state.rows.length);
        renderResults();
        renderSummary();
      }
      toast("Evaluation completed", "success");
    } catch (err) {
      if (err.name === "AbortError") toast("Evaluation cancelled", "warn");
      else toast(err.message, "error");
    } finally {
      state.controller = null;
      $("#run-btn").disabled = false;
      $("#cancel-btn").hidden = true;
      $("#download-btn").disabled = !state.results.length;
      renderResults();
    }
  }

  function download() {
    const lines = [["question", "expected_answer", "answer", "verdict"].join(",")];
    state.rows.forEach((r, i) => {
      const res = state.results[i];
      if (res) lines.push([r.question, r.expected_answer, res.answer, res.verdict].map(csvCell).join(","));
    });
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (state.fileName || "testbook.csv").replace(/\.csv$/i, "") + "_evaluated.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function readFile(file) {
    if (!file) return;
    if (!/\.csv$/i.test(file.name)) return toast("Please select a .csv file.", "error");
    const reader = new FileReader();
    reader.onload = () => loadCsv(file.name, reader.result);
    reader.readAsText(file);
  }

  async function main() {
    const user = await window.Shell.bootUser("admin");
    if (!user) return;
    if (!isAdmin(user)) {
      renderAccessDenied({ status: 403, problem: { status: 403, title: "Forbidden", detail: "The Evaluation page is reserved to administrators (okd_admin).", instance: "/admin" } });
      return;
    }
    const input = $("#csv-input");
    const dz = $("#dropzone");
    $("#browse-btn").addEventListener("click", () => input.click());
    input.addEventListener("change", () => readFile(input.files[0]));
    $("#sample-btn").addEventListener("click", () => loadCsv("sample_testbook.csv", window.MockData.SAMPLE_TESTBOOK));
    dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("over"));
    dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); readFile(e.dataTransfer.files[0]); });
    $("#run-btn").addEventListener("click", run);
    $("#cancel-btn").addEventListener("click", () => state.controller && state.controller.abort());
    $("#download-btn").addEventListener("click", download);
  }

  document.addEventListener("DOMContentLoaded", main);
})();
