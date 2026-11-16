# rag-agent

Agentic RAG (Retrieval-Augmented Generation) solution that answers user questions by retrieving
and citing internal documentation.

## About this repository

This repository is an **anonymized excerpt** of a real project. The original codebase contains
sensitive information (customer names, business domain, internal documents, infrastructure and
deployment details), which has been removed or replaced with placeholders such as `[censored]`.

What is published here is only the **intellectual property** of the solution: the architecture,
the backend design and the agentic retrieval logic. Customer data, prompts tied to the business
domain, infrastructure-as-code and CI/CD configuration are not included.

The original frontend has been **replaced by a static mockup** that shows the product features in a
simple way, without connecting to a real backend.

## Repository layout

| Path | Content |
|---|---|
| `backend/` | FastAPI service (Python 3.13, `uv`, `just`) — agent, knowledge base, feedback, user info |
| `frontend/mockup/` | Static HTML/CSS/JS mockup of the chat UI, driven by mocked data |

## Backend

A FastAPI service exposing a chat assistant on top of a vector knowledge base
(PostgreSQL + pgvector), with LLMs accessed through LangChain / LangGraph.

Main capabilities:

- **Tool-calling agent** (`app/agent/`) — a LangGraph loop (`agent` ⇄ `tools`) where the LLM decides
  how to answer:
  - `search_knowledge_base(query, category?)` runs a semantic search, then an LLM judge filters out
    irrelevant chunks before they reach the model;
  - `answer_without_search(reason)` is an explicit, logged exit for small talk and out-of-scope requests;
  - when a search returns nothing, the agent can refine the query or change the category filter,
    up to a fixed number of tool calls per turn.
- **Streaming** over the [AG-UI](https://docs.ag-ui.com) protocol (`POST /agent/chat/stream`, SSE),
  so clients see the full reasoning flow: interim text, tool calls, tool results and the final answer.
- **Conversation memory** per thread (`threadId` = chat session).
- **Feedback** (`POST /feedback`) — thumbs up / down on each answer, with an optional comment on
  negative votes; one vote per message.
- **User info** (`GET /userinfo`) — identity and groups, used for role-based features (admin).
- **Evaluation** (`app/evaluation/`) — batch evaluation of a question testbook against the assistant.
- Errors returned as RFC 7807 problem details.

Common commands are in `backend/justfile` (`just dev`, `just test`, `just lint`, …).

> Running the backend end-to-end requires cloud credentials, an LLM gateway and a populated
> knowledge base, which are not part of this repository.

## Frontend mockup

`frontend/mockup/` is a dependency-free, no-build mockup. All data is fake and generic; there are no
calls to a real backend.

### How to open it

**Online, via raw.githack** (nothing to install):

<https://raw.githack.com/GennariAl/rag-agent/main/frontend/mockup/index.html>

GitHub serves repository files as plain text, so opening `index.html` (or its *Raw* view) from
github.com only shows the source code. [raw.githack](https://raw.githack.com) serves the same files
with the correct content type, so the page runs and its CSS/JS are loaded. Notes:

- the URL parameters below work here too, e.g. `…/index.html?role=admin`;
- raw.githack caches files, so a freshly pushed change may take a few minutes to show up;
- it only works while the repository is public.

**Locally**: clone the repository and open `frontend/mockup/index.html` with a double click.
No web server is needed.

What it shows:

- **Chat** with streaming answers rendered as markdown.
- **Activity trace** for each answer: what the agent is doing live ("Searching *…*", "6 relevant
  passages found", "No relevant content, retrying…", "Answering directly"), collapsed into a summary
  chip once the answer starts.
- **Sources**: documents used for the answer, deduplicated, with categories, page numbers and links.
- **Feedback**: 👍 / 👎 with an optional comment, locked after voting.
- **Chat history** in a collapsible sidebar (stored in the browser), new chat, restore and delete.
- **Error handling**: error bubble with Retry, Stop button, network error toast, access-denied screens.
- **Admin Evaluation page** (`admin.html`): upload a testbook CSV, run it, follow progress, review
  verdicts and download the enriched CSV.

The mock emits the same AG-UI event sequence as the real `/agent/chat/stream` endpoint, so the
client-side reducer in `app.js` is written against the real protocol.

Try these:

| Action | Result |
|---|---|
| Click a suggested question | Runs one of the scripted scenarios (single search, refined search, small talk, out of scope) |
| Ask a question containing `error` | Simulated run error; Retry succeeds |
| Ask a question containing `network` | Simulated network failure |
| Add `?role=admin` to the URL | Admin user: the **Admin** link appears |
| Add `?auth=401` or `?auth=403` | Session-expired / access-denied screens |
