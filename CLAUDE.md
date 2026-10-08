# Epistrel Console

Django web app: the human-facing test harness for the Epistrel Engine (nathanpond/Epistrel, a separate repo). Its purpose is to drive and inspect Engine capabilities. Console features mirror Engine capabilities and talk to the Engine only through its public REST API. The Engine's `docs/` hold the authoritative design and requirement IDs (`FR-`/`NFR-`).

- Tooling: `uv sync`, `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` (strict, django-stubs).
- Settings come from the environment (`DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`). Never commit a real secret.

## n8SDLC project

This project is managed by the n8SDLC workflow (GitHub Issues = the plan; `/n8-stat` shows where things stand). If a change made in this session deviates from what planned issues assume — different library, provider, architecture, dropped/added scope, or amending a declared invariant below — do two things before finishing:
1. Append an `## Ad-hoc` entry to `.n8/decisions.md` (format documented in that file's header) naming the change, the why, and the milestones/issues likely affected.
2. Tell the user which future milestones may now have stale plans and suggest running `/n8-replan`.

Background work (shells, monitors, background subagents) follows `reference/background.md` in the n8SDLC plugin. Every launch is bounded and announced with what it's for and when it should finish. Overdue work is checked, not waited on. A turn never ends with work still running unless the reply names it. `/n8-subs` lists everything live.

Separately: if a `/n8-*` skill's own instructions failed, misled you, or were silent on something this session, tell the user and offer `/n8-feedback` — it packages the learning as an issue on the plugin repo, stripped of project specifics, and sends nothing until the user has reviewed the exact text.
