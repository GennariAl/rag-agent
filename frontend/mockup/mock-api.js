/*
 * Fake backend for the mockup. Mirrors the real contract:
 *   GET  /userinfo               -> {name, surname, email, groups[]}
 *   POST /feedback               -> 204 | 409 (already voted)
 *   POST /agent/chat/stream      -> AG-UI events (here: an async generator)
 * Errors are thrown as ApiError carrying an RFC 7807 problem body.
 */
(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const sleep = (ms, signal) =>
    new Promise((resolve, reject) => {
      const t = setTimeout(resolve, ms);
      if (signal) {
        signal.addEventListener("abort", () => {
          clearTimeout(t);
          reject(new DOMException("Aborted", "AbortError"));
        }, { once: true });
      }
    });

  class ApiError extends Error {
    constructor(problem) {
      super(problem.detail || problem.title);
      this.problem = problem;
      this.status = problem.status;
    }
  }

  const problem = (status, title, detail, instance) => ({ type: "about:blank", title, status, detail, instance });
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : "id-" + Math.random().toString(36).slice(2) + Date.now().toString(36));

  async function getUserInfo() {
    await sleep(250);
    const auth = params.get("auth");
    if (auth === "401") throw new ApiError(problem(401, "Unauthorized", "Your session has expired. Please sign in again.", "/userinfo"));
    if (auth === "403") throw new ApiError(problem(403, "Forbidden", "You don't have access to the Knowledge Assistant.", "/userinfo"));
    const role = params.get("role") === "admin" ? "admin" : "editor";
    return structuredClone(window.MockData.USERS[role]);
  }

  const votes = new Set(JSON.parse(localStorage.getItem("ka-mock-votes") || "[]"));

  async function postFeedback({ sessionId, messageId, feedback, comment }) {
    await sleep(300);
    const key = `${sessionId}:${messageId}`;
    if (votes.has(key)) throw new ApiError(problem(409, "Conflict", "Feedback for this message has already been submitted.", "/feedback"));
    votes.add(key);
    localStorage.setItem("ka-mock-votes", JSON.stringify([...votes]));
    console.info("[mock] POST /feedback", { sessionId, messageId, feedback, comment });
    return null; // 204 No Content
  }

  /** Split a string into small chunks to emulate token streaming. */
  function tokens(text) {
    return text.match(/\S+\s*|\s+/g) || [];
  }

  const failedOnce = new Set();

  /**
   * Emulates POST /agent/chat/stream. `input` is a RunAgentInput:
   * {threadId, runId, messages:[{id, role, content}], state, tools, context, forwardedProps}
   */
  async function* streamAgent(input, signal) {
    const lastUser = [...input.messages].reverse().find((m) => m.role === "user");
    const question = lastUser ? lastUser.content : "";
    let key = window.MockData.pickScenario(question);

    if (/network/i.test(question)) {
      await sleep(400, signal);
      throw new TypeError("Failed to fetch");
    }
    // The error scenario fails only once per question so that "Retry" succeeds.
    if (key === "error") {
      if (failedOnce.has(question)) key = "kbSingle";
      else failedOnce.add(question);
    }

    const script = window.MockData.SCENARIOS[key](question);
    const { threadId, runId } = input;
    const base = { timestamp: Date.now() };
    const ev = (e) => ({ ...base, ...e, timestamp: Date.now() });

    yield ev({ type: "RUN_STARTED", threadId, runId });
    await sleep(300, signal);

    let lastAiId = null;
    for (const iteration of script) {
      yield ev({ type: "STEP_STARTED", stepName: "agent" });
      await sleep(500, signal);
      const aiId = "msg-" + uuid();
      lastAiId = aiId;

      const text = iteration.interim || iteration.answer;
      if (text) {
        yield ev({ type: "TEXT_MESSAGE_START", messageId: aiId, role: "assistant" });
        for (const t of tokens(text)) {
          yield ev({ type: "TEXT_MESSAGE_CONTENT", messageId: aiId, delta: t });
          await sleep(iteration.answer ? 28 : 45, signal);
        }
        yield ev({ type: "TEXT_MESSAGE_END", messageId: aiId });
      }

      if (iteration.tool) {
        const toolCallId = "toolu_" + uuid().slice(0, 12);
        const argsJson = JSON.stringify(iteration.tool.args);
        yield ev({ type: "TOOL_CALL_START", toolCallId, toolCallName: iteration.tool.name, parentMessageId: aiId });
        for (let i = 0; i < argsJson.length; i += 12) {
          yield ev({ type: "TOOL_CALL_ARGS", toolCallId, delta: argsJson.slice(i, i + 12) });
          await sleep(25, signal);
        }
        yield ev({ type: "TOOL_CALL_END", toolCallId });
        yield ev({ type: "STEP_FINISHED", stepName: "agent" });

        yield ev({ type: "STEP_STARTED", stepName: "tools" });
        await sleep(iteration.tool.name === "search_knowledge_base" ? 1400 : 300, signal);
        if (iteration.tool.fail) {
          yield ev({ type: "RUN_ERROR", message: "Sorry, something went wrong. Please try again." });
          return;
        }
        const content = iteration.tool.rawResult ?? JSON.stringify(iteration.tool.result);
        yield ev({ type: "TOOL_CALL_RESULT", messageId: "tool-" + uuid(), toolCallId, content, role: "tool" });
        yield ev({ type: "STEP_FINISHED", stepName: "tools" });
      } else {
        yield ev({ type: "STEP_FINISHED", stepName: "agent" });
      }
    }

    // Mirrors _sanitize_agent_snapshot: references always empty, last message id only.
    yield ev({ type: "STATE_SNAPSHOT", snapshot: { references: [], messages: lastAiId ? [{ id: lastAiId }] : [] } });
    yield ev({ type: "RUN_FINISHED", threadId, runId });
  }

  /** Hypothetical evaluation endpoint: EvaluationService exists in the backend but has no route. */
  async function evaluateQuestion(row, signal) {
    await sleep(600 + Math.random() * 900, signal);
    const scenario = window.MockData.pickScenario(row.question);
    const script = window.MockData.SCENARIOS[scenario === "error" ? "kbSingle" : scenario](row.question);
    const answer = script[script.length - 1].answer || "";
    const r = Math.random();
    const verdict = scenario === "outOfScope" ? "PASS" : r > 0.3 ? "PASS" : r > 0.12 ? "PARTIAL" : "FAIL";
    return { answer: answer.replace(/[*`>#]/g, "").replace(/\n+/g, " ").trim(), verdict };
  }

  window.MockApi = { ApiError, uuid, getUserInfo, postFeedback, streamAgent, evaluateQuestion };
})();
