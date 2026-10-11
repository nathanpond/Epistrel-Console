# Decision log

Append-only. One `##` section per skill run, entries in chronological order. Record real decisions (choices between alternatives, assumptions, deviations from plan), not routine actions.

```markdown
## /n8-exec M1 — YYYY-MM-DD

- **Decision:** <what was chosen>
  **Why:** <reasoning; cost if wrong>
  **Issue:** #N
```

## Ad-hoc entries (the drift ledger)

Changes made outside the n8SDLC commands that deviate from what planned issues assume get an `## Ad-hoc` section. `/n8-replan`, `/n8-exec`'s preflight, and `/n8-stat` read these to detect stale plans. When `/n8-replan` processes an entry it appends `— reconciled by /n8-replan <date>`.

```markdown
## Ad-hoc — YYYY-MM-DD

- **Change:** <what changed, e.g. auth provider switched from Google to Okta>
  **Why:** <reason>
  **Affects:** <milestones/issues whose plans may now be stale>
```

---

## /n8-init — 2026-10-07

- **Decision:** Renamed the default branch from `master` to `main` before the first commit.
  **Why:** n8SDLC conventions and the `main` ruleset assume `main`; free to change with no history.
- **Decision:** Python tooling is uv + ruff + mypy (strict) + pytest, Python ≥3.12 (dev pinned to 3.14).
  **Why:** Matches the Engine repo's toolchain (docs/02 in nathanpond/Epistrel specifies Python 3.12+ with mypy). Django typing via django-stubs, tests via pytest-django.
- **Decision:** Security findings are logged as public `security` issues.
  **Why:** Same policy as the Engine: open-source, self-hosted, no production deployment to protect.
- **Decision:** Moved `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` to environment variables before the first public push.
  **Why:** `startproject` hardcodes a secret key; publishing it would make it a known value. A dev-only fallback key is accepted only while DEBUG is on.

## /n8-roadmap — 2026-10-07

- **Decision:** Milestones M0–M10 mirror the Engine's one-for-one; each Console M<n> depends on Engine M<n> being verified. 26 epics, one per Engine capability that gets a Console surface, each naming the Engine epics it mirrors.
  **Why:** The Console's purpose is to see and debug the Engine working; its roadmap has no independent shape.
- **Decision:** Single-operator dev tool with Django auth; the logged-in user is the requesting principal. Play view and inspector panels side by side.
- **Decision:** Django templates + HTMX, no JS build step. Console DB holds only its own concerns.
- **Decision:** No accessibility work and no 508 audit.
  **Why:** User's call for a single-operator dev tool.
- **Decision:** No project invariants.
  **Why:** User judged the CI and maintenance cost unjustified for a dev console; CLAUDE.md guidance (REST-only, env secrets) covers the one rule that matters, and `/n8-audit` still checks it.
- **Decision:** Deployment = compose alongside the Engine `edge` image with Postgres; SQLite for dev; `v*` tags publish image + release. Nothing hosted.
- **Decision:** CI tests use a fake Engine built from the Engine's OpenAPI schema, plus one live smoke job against the Engine `edge` service container.
  **Why:** Catches API drift in the Console's own CI without making every test depend on a running Engine.
- **Decision:** M9 "Bug fixes & testing" left unplanned; M10 Audit.

## /n8-plan M0 — 2026-10-10

- **Decision:** M0 sliced into four stories: Engine settings & dev runserver (#29) → status page & /health (#30) → container image (#31) → compose stack (#32). M0 owns the dev environment and the runtime half of `edge`; publishing, gates and CI steps stay in M1 (epic #2).
  **Why:** The roadmap assigned CI/publishing to M1; M0's config surface is the two items no M1 epic owns.
- **Decision:** Console port 7739 for runserver and compose (owner: "a more obscure port"); 7739 is unregistered with IANA and below ephemeral ranges.
- **Decision:** Missing/invalid `CONSOLE_ENGINE_URL` and unknown `CONSOLE_*` variables refuse start; `CONSOLE_ENGINE_SERVICE_KEY` optional until the Engine enforces service auth (owner). Recorded on epic #3.
  **Why:** Refusal lives in runserver/WSGI/ASGI, not a Django system check, so makemigrations, migrate, mypy and pytest run without an Engine.
- **Decision:** `/health` always 200; status page shows Healthy / Unhealthy / Unreachable (owner). Probe timeout 3 s.
  **Why:** The Console diagnoses a down Engine; the Engine's own DB check takes up to 2 s.
- **Decision:** `DJANGO_SECRET_KEY` auto-generated on first start into the data volume when unset (owner chose option C over a committed dev key or a required `.env`).
- **Decision:** Compose: Console data is SQLite on a volume, bound to 127.0.0.1 only, no restart policy, Engine/db publish no host ports (owner + planner).
- **Decision:** Epic #2 gained ACs for edge/production environment gates, secret scanning, Dependabot ecosystems, and compose consuming the published Console `edge` image.
  **Why:** The coverage check found these M1-assigned items had no owning AC.

## /n8-plan M1 — 2026-10-10

- **Decision:** M1 sliced into eight stories: PR gate (#35), live Engine smoke (#36), publish edge (#37), release (#38), rollback (#39), typed client + recorded schema + fake Engine (#34), daily drift issue (#40), status-page API line (#41). CI workflows mirror the Engine's `ci.yml`, `publish-edge.yml`, `release.yml`, `rollback.yml` and tag ruleset.
  **Why:** The Engine's M1 already proved this delivery shape; mirroring it keeps the two repos operable the same way.
- **Decision:** The M1 client covers only `/health` (and `/openapi.json`); each later Console milestone adds the endpoints its Engine milestone ships (owner).
- **Decision:** Engine API drift is reported by a daily scheduled check that opens one self-closing issue and never blocks a PR; the live-Engine smoke does block PRs and releases, so a broken Engine `edge` blocks Console merges (owner, "2c").
- **Decision:** The live smoke is skipped for edge publishing and edge rollback, so an Engine outage cannot freeze the Console's `edge` (owner, round two).
- **Decision:** The requesting principal is `console:<username>`; non-ASCII is percent-encoded as UTF-8 (owner + planner at the gate).
- **Decision:** The status page and `/health` show whether the live Engine's API matches the recorded schema; informational only (owner).
- **Decision:** Console versions track the Engine's: Console `vX.Y.Z` requires Engine image `X.Y.Z` and is smoke-tested against it; releases name the Engine build tested; Console-only fixes reach users via `edge` until the Engine's next release (owner + planner).
  **Why:** One version number tells a user which Console goes with which Engine.
- **Decision:** Live proofs that need `workflow_dispatch`, a tag, the live ruleset or `main`'s history run right after the milestone PR merges; evidence goes in issue comments.
  **Why:** GitHub only dispatches workflows that exist on the default branch.
- **Decision:** Filed #42 (`needs-triage`): pass a service credential to the Engine in CI and compose once Engine #44 enforces auth.
  **Why:** Today's Engine refuses unknown `EPISTREL_*` variables, so M1 cannot set it early; without the follow-up every Console PR breaks when #44 reaches `edge`.
- **Decision:** Epic #2's "pinned actions" and two epic #3 ACs reworded to match the owner's decisions (coverage check).
