# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. It holds the **project-wide context** shared by both teams; the workspace-specific rules live in their own files:

- **Backend:** [backend/CLAUDE.md](backend/CLAUDE.md) — FastAPI service conventions, knowledge base, assistant.
- **Frontend:** [frontend/AGENTS.md](frontend/AGENTS.md) and [frontend/README.md](frontend/README.md) — React/TypeScript SPA rules and patterns.

These files are loaded automatically by Claude Code when you read or edit a file inside the corresponding workspace, on top of this root file.

## Project context

[censored]


## Repository Layout

Monorepo with three top-level workspaces:

- `backend/` — FastAPI (Python 3.13) service `okd`, managed by `uv` and run via `just`. See [backend/CLAUDE.md](backend/CLAUDE.md).
- `frontend/` — React 19 + TypeScript SPA built with Vite, managed by `pnpm`. See [frontend/AGENTS.md](frontend/AGENTS.md) and [frontend/README.md](frontend/README.md).

**Backend and frontend are developed by separate teams.** Work in the workspace relevant to the task and do not modify the other workspace (or `infrastructure/`) unless explicitly asked. The workspace-specific conventions are mandatory — read the relevant file above before adding code.

## Language

Always write code, comments, docstrings, log messages, and `print`/output strings in **English** — this includes variable names, function names, class names, file names, and any text that ends up in the repository. Use **Italian** in all conversation replies and plans (anything not written to a file), never inside source files.

## CI

`.gitlab-ci.yml` is a thin router that includes pipelines from `.gitlab/ci/pipelines/{review,development,qualification,production}` based on the trigger (MR, default branch, `release/*` branch, tag). Backend/infrastructure changes are gated separately from frontend changes.
