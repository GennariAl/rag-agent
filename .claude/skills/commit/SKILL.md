---
name: commit
description: >-
  Generate a Conventional Commits-compliant commit message from the currently
  staged changes, show it to the user for approval or edits, then run
  `git commit` and `git push`. Use this whenever the user wants to commit and/or
  push their work — e.g. "/commit", "commit this", "commit and push", "make a
  commit message", "committa le modifiche in staging". This skill owns the
  team's commit-message convention, so prefer it over an ad-hoc `git commit`
  whenever a commit is being created.
---

# Commit

Create a well-formed [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
message from the **staged** changes, get the user's sign-off (with the option to
edit), and only then commit and push.

The point of this skill is that commit messages are the durable, human-readable
record of *why* a change happened. A machine-generated message that the user
rubber-stamps is worse than useless, so the flow is deliberately
**propose → let the user correct → confirm → act**. Never commit or push before
the user has explicitly approved the final message.

## Workflow

### 1. Read only what is staged

`git commit` records exactly what is in the staging area, so the message must
describe *that* — not the working tree at large.

```bash
git diff --cached --stat   # overview of staged files
git diff --cached          # full staged diff, to understand the change
```

If the staged diff is **empty**, stop. Tell the user there is nothing staged and
ask them to `git add` what they want to include. Do **not** run `git add`
yourself or commit an empty set — the user decides what goes into the commit.

It's fine to also glance at `git status` to mention unstaged files that the user
might have forgotten to stage, but never stage them automatically.

### 2. Draft the message

Infer the intent from the diff and write a Conventional Commits message. Format:

```
<type>(<scope>): <description>

<optional body>

<optional footer>
```

- **type** — one of: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`,
  `test`, `build`, `ci`, `chore`, `revert`. Pick by *intent*, not by which files
  changed (a bug fixed inside a test file is still `fix`, not `test`).
- **scope** — optional, in parentheses. Use a short area name derived from the
  change (e.g. a module/feature/package). Omit it if no single scope fits.
- **description** — imperative mood, lowercase start, no trailing period. Keep it
  short: aim for ≤ 50 chars, and never go past 72 (e.g. "add glossary query
  expansion", not "added" / "adds").
- **body** — never include it. Always produce a subject-only commit.
- **BREAKING CHANGE** — if the change breaks a public contract, either append
  `!` after the type/scope (`feat(api)!: ...`) or add a
  `BREAKING CHANGE: <explanation>` footer (do both if it helps clarity).

Prefer brevity everywhere: a concise, accurate subject beats a verbose one.

Do **not** add a `Co-Authored-By` (or any authorship) footer.

**Never describe changes to Claude/agent config files in the message.** Files like
`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, and anything under `.claude/` (skills,
settings) may be part of the staged commit, but the message must not mention them —
neither in the subject nor the body. Base the message only on the *other* staged
changes. If those config files are the **only** thing staged, tell the user there's
nothing to describe and ask how they'd like to phrase the message rather than
inventing a subject about them.

If the staged diff clearly bundles several unrelated changes, say so and suggest
splitting into multiple commits — but follow the user's decision.

**Examples**

Input: added JWT validation to the login endpoint
Output: `feat(auth): validate JWT on login`

Input: fixed an off-by-one that dropped the last search result
Output: `fix(search): include final result in hybrid ranking`

Input: renamed variables and reflowed a function, no behavior change
Output: `refactor(assistant): clarify judge-node variable names`

### 3. Show it and wait

Present the full proposed message in a fenced block so it's easy to read, then
ask the user to **approve**, **edit**, or **cancel**. If they request changes,
revise and show the updated message again — iterate until they approve. Treat
anything short of a clear approval as "not yet".

### 4. Commit and push

Once — and only once — the user approves, commit with the exact approved message
and push:

```bash
git commit -m "<subject>" -m "<body>"   # second -m only if there is a body
git push
```

- Use separate `-m` flags for subject and body rather than embedding newlines, so
  the formatting is reliable. For a multi-paragraph body use one `-m` per
  paragraph.
- If the branch has no upstream, `git push` will fail; in that case push with
  `git push -u origin HEAD` (confirm this is what they want first).
- If the current branch is the default branch (`main`/`master`), warn the user
  before pushing — they may want a feature branch — and proceed only if they
  confirm.

Report the result: the commit hash/subject and the push outcome. If the commit
or push fails, show the error and stop rather than retrying blindly.
