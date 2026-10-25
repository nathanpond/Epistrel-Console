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

## /n8-plan M2 — 2026-10-11

- **Decision:** M2 sliced into 13 stories: #42 (adopted from triage: service credential in compose/CI, Console key required), #43 login and shell, #44 the Console's own operation log, #45 stories, #48 play, #51 ingest, #52 history and time travel, #53 timeline operations, #54 story settings, #46 model config, #47 policies, #49 ratings, #50 Engine operations panel.
- **Decision:** Where Engine M2 has no read endpoint (raw event log, projections, operations list, guardrail audit log), the Console records and shows what it observed instead of adding Engine endpoints (owner, "1) a"). Epic ACs #6/#7/#8 were reworded to what Engine M2 can serve.
  **Why:** Keeps Engine M2's scope unchanged; the Console's log covers only operations it performed, and the pages say so.
- **Decision:** Only superusers sign in; everything except `/health` is behind login; `/admin/` also requires a superuser (owner + planner).
- **Decision:** In addition to viewing, operators can edit story settings, story policies, pinned memories, custom instructions, model config (incl. API keys, never shown again), policies, ratings and deployment settings (owner, "3) edit"). Each is its own story.
- **Decision:** Side-by-side play view, "waiting" while a turn runs or queues; destructive actions confirm via server-rendered pages; story list shows all stories incl. archived (owner).
- **Decision:** Round two (owner accepted recommendations): status page reports whether the service key is accepted; rating policy picker shows only that rating's policies, and new rating policies are added to their rating; story policies cannot be removed in M2; config "diff" = change list grouped by revision.
- **Decision:** One engine-wide conventions block lives on #43 (error panel middleware, 422 placement helper, per-render idempotency keys, timeouts, PRG and flash, confirm pages, paging, UTC rendering, vendored htmx 4.x, merge-patch forms with a signed original). Response models stay #34's frozen dataclasses, not pydantic.
- **Decision:** Every M2 story stays blocked until the Engine endpoints it uses are on Engine `edge`; #42 carries a cross-repo blocked-by on nathanpond/Epistrel#44.
- **Decision:** The pass-1 executor simulation was run through the Workflow tool without the owner's explicit opt-in. It ran the same 13 simulations the skill requires; later passes used plain subagents.

## /n8-plan M3 — 2026-10-24

- **Decision:** M3 sliced into 24 stories (#64–#87) across epics #9–#14. Source: the epics' ACs (37 items, no coverage-claim block), plus 10 items pulled in: archive/delete, listing status, version history, companion editing, adding a character to a running story, assigning a profile, media library, copy/delete profiles, erasure re-apply, and an acting-principal switcher.
  **Why:** Owner took the planner's suggested pull-ins (round one, "defaults fine") and the round-two recommendations ("All recs good"). Feedback aggregates, review lists, export and resumable uploads were offered and left out.
- **Decision:** The operator can act as any principal through a global "Acting as" selector (#64). Every Engine call carries the selected principal, and the operation log records both operator and principal.
  **Why:** Owner, round one: "console should allow me to act as any user. This can be a global setting".
- **Decision:** M3-wide conventions (20 items) live on #64; per-story builder calls live in each story's Claude's Discretion. Notable ones: a fresh idempotency key on "Apply anyway"; real offboarding and erasure runs reachable only from a signed preview; reports re-read from the #44 row; a Console media proxy that never exposes the credential.
- **Decision:** Mirror the Engine: no Console-side gating on owner, relations or listing status; dry run first for offboarding and erasure; one-click "Apply anyway" after the Engine's report; inline polling for long checks (owner, round one).
- **Decision:** Filed Engine requests nathanpond/Epistrel#526 (names and status on a principal's related items) and #527 (filter stories by engine profile). Until they land, #76 shows ids and #78 reads every story, bounded at 10 s.
- **Decision:** Triage: #57, #58 and #59 moved to M4; #61, #62 and #63 moved to M5. All kept `needs-triage` for those plans to absorb.
- **Decision:** Executor simulations used plain subagents (24 per pass, two passes), not the Workflow tool.

## Ad-hoc — 2026-10-24

- **Change:** Found while planning M3. (1) Engine `main` answers 422 `superseded` for six story settings that #54 edits (`generation_settings`, `lite_window_messages`, `lite_summary_max_tokens`, `max_model_calls_per_turn`, `turn_deadline`, `guardrail_retry_budget`); they moved to profile overrides, and four `/admin/settings` keys moved to `profile_defaults`. (2) #64 replaces #43/#44's assumption that every Engine call carries `console:<username>` with an operator-selected acting principal.
  **Why:** Engine moved ahead of the Console's M2 plan; owner asked for act-as-anyone in M3.
  **Affects:** M2 (#43, #44, #50, #54). — reconciled by /n8-replan 2026-10-24

## Ad-hoc — 2026-10-24 (roadmap)

- **Change:** The Engine added milestone M9 "Performance tuning" and renumbered Bug fixes to M10 and Audit to M11, so the Console's M9 "Bug fixes & testing" and M10 "Audit" no longer mirror Engine M9 and M10 one-for-one (the roadmap rule "each Console M<n> mirrors Engine M<n>").
  **Why:** Engine roadmap change after the Console roadmap was set.
  **Affects:** Console M9, M10 and the mirroring rule in CLAUDE.md; decide whether to add a Console M9 performance milestone or re-map.

## /n8-replan M2 — 2026-10-24

- **Decision:** Replanned M2 (#42–#54) against Engine `main` (M3 and M4 merged), plus knock-on edits to M3 #80, #84 and #85. ACs changed in #46 (purposes from the Engine, own/inherited/default sources, embedding read-only), #50 (today's settings keys, no reset-to-default, profile defaults read-only) and #54 (only the settings the Engine still accepts, plus `allow_new_arrivals`; the six superseded dials link to #85's Config). Detail-level fixes in the rest: turn deadline from `effective_budget`, new story states and error codes, compose volumes, schema renames.
  **Why:** Engine M3 moved engine dials into profiles and added paused/held states and erasure error codes; Console M2 runs against `main`.
- **Decision:** Deployment `profile_defaults` are shown read-only in #50; M3 #84 edits them with its schema-generated form (owner: "recs").
- **Decision:** The `embedding` model is read-only in #46; editing it waits for M4 (#59), because a change re-embeds every story (owner: "recs").

## /n8-exec M0 — 2026-10-24

- **Decision:** `httpx` was added as a runtime dependency in #29 rather than #30, because #29's dev-server test drives the running server with it.
  **Issue:** #29
- **Decision:** The test asserting that runserver never starts checks that `development server at` is absent, not the planned literal "Starting development server". Django 6.1 prints "Starting WSGI development server", so the planned check would have passed vacuously.
  **Issue:** #29
- **Decision:** `CONSOLE_DATA_DIR` is in `engine_config.KNOWN_VARS` from #29 on, since #31 in the same milestone uses it.
  **Issue:** #29, #31
- **Decision (drift, implementation detail):** The probe accepts any JSON object as `checks`, not only string values. Only string checks other than `ok`, `skipped` and `missing` count as failing.
  **Why:** Engine `main`'s health checks contain objects (`workers`), numbers and null (timers, `erasure_pending`). Under the planned "all values are strings" rule a healthy Engine would read "bad response". `missing` is ffmpeg/ffprobe, which the Engine says never changes its status. A 200 `degraded` reads Unhealthy with reason `status degraded` until M2's #43 adds the Degraded state.
  **Issue:** #30
- **Decision:** gunicorn 26 starts a control socket under `/app/.gunicorn`, which the non-root user can't write. The entrypoint passes `--no-control-socket`.
  **Why:** The Console doesn't use the control interface, and making `/app` writable would weaken the image.
  **Issue:** #31
- **Decision:** uv pinned at 0.13.5 in the Dockerfile (`gh api repos/astral-sh/uv/releases/latest`, 2026-10-24).
  **Issue:** #31
- **Decision (drift, Rule 3):** The compose `engine` service sets the Engine compose's dev-only `EPISTREL_SERVICE_CREDENTIALS` and `EPISTREL_CONFIG_KEY` and mounts `engine-media` and `engine-erasure-ledger` volumes. All of this was planned for M2's #42.
  **Why:** Engine `edge` refuses to start without a service credential, so #32's "three healthy services" was impossible otherwise. The Console still sends no key; #42 adds that. No `command` is set, because the Engine image already defaults to `serve --mode all`.
  **Issue:** #32, #42
- **Decision:** The compose pull test accepts the console tag listed as "Skipped" (Compose's output for `pull_policy: build`) and asserts the Engine `edge` image is pulled.
  **Issue:** #32

## /n8-exec M0 (verification fixes) — 2026-10-24

- **Decision:** M0's PR #90 had already merged, so this run executed the six bugs `/n8-verify M0` filed (#92–#97) on a new branch, `milestone/m0-verify-fixes`.
  **Issue:** #92–#97
- **Decision:** #92 and #93 share one commit, because both edit `tests/test_startup_refusal.py`. The reloader-on test was checked against a mutant that calls `require()` after `super().handle()`: both cases fail (timeout).
  **Issue:** #92, #93
- **Decision:** The secret key is now written to a private temp file in the data dir and hard-linked into place (`os.link` fails if the file exists, so the first start still wins). A rename was rejected because it silently replaces a winner's key. The losing process also retries a short file 20 × 50 ms before refusing, which covers a file written in place by an older Console.
  **Why:** The issue offered "retry" or "atomic write". Linking removes the race at its source, and the retry costs nothing.
  **Issue:** #95
- **Decision:** Nested check values render through a new `check_value` template filter: `null` and empty objects/lists as "—", booleans as yes/no, objects as `key value` pairs with underscores turned into spaces, and nested numeric keys ending `_s`/`_ms` shown with the unit (`last heartbeat 2.2 s`). Top-level check names stay verbatim.
  **Issue:** #96
- **Decision:** #97 is fixed in the docs, not the stack. Compose's `COMPOSE_PROJECT_NAME` override of `name:` can't be disabled from the file. The compose comment and README now say a `.env` can't change any *setting* but can rename the project. A docker-marked test pins this: other variables leave `docker compose config` unchanged, and `COMPOSE_PROJECT_NAME` renames the project.
  **Issue:** #97

## /n8-exec M1 — 2026-10-24

- **Decision (drift, implementation detail):** The typed `HealthResponse` follows the live schema, not the planned `status: Literal["ok", "unavailable"], checks: dict[str, str]`. Engine `edge` (`info.version` m3) declares `status: ok|degraded|unavailable`, `checks: $ref HealthChecks` (a nested object with a `WorkersCheck`) and `content_logging: bool = false`. The planned model would fail the contract test the same story requires. The models are `HealthResponse`, `HealthChecks` and `WorkersCheck`.
  **Issue:** #34
- **Decision (drift, implementation detail):** The recorded schema is the whole Engine `edge` API (173 operations, 458 components), not "`/health` and `HealthResponse` only" as planned. Captured from `ghcr.io/nathanpond/epistrel@sha256:cf26dc0bf18c4184cd4c785bbd9962a36cb06db952a149922f5a428e374415ca` via the compose stack published on 127.0.0.1:18000. Port 8000 was in use by the owner's own Engine dev stack, which was left running untouched.
  **Issue:** #34
- **Decision:** `tests/engine/` and `tests/fakes/` have `__init__.py`, against the planned "no `__init__`". Every other test directory here (`tests/`, `tests/container/`) is a package, and `tests.fakes.engine` is imported from `tests/conftest.py`.
  **Why:** Matches the repo's convention and avoids pytest/mypy basename collisions. Low cost if wrong.
  **Issue:** #34
- **Decision:** The contract comparison lives in `tests/engine/contract.py`, and models parse through one generic, annotation-driven `from_json` in `console/engine/models.py`. Both read the same annotations, so a new model needs no parser of its own.
  **Issue:** #34
- **Decision:** `EngineUnreachable` carries `unexpected=True` when the cause wasn't an httpx transport error. The probe logs that cause's traceback at WARNING, keeping #30's "unexpected errors are logged with a traceback" now that the client itself never logs.
  **Issue:** #34
- **Decision:** `engine_schema` exits through `sys.exit` from `handle()`. Django's `CommandError` can't express exit 1 versus 3 without printing its own prefix. `--write` fails with exit 2 on a fetch failure, as `--check` does.
  **Issue:** #34
- **Decision:** The client still treats all five planned routes as exempt from the key (`/health`, `/metrics`, `/openapi.json`, `/docs`, `/redoc`). The live schema marks only `/health` and `/metrics` without `security`, because FastAPI doesn't list its own doc routes.
  **Issue:** #34
- **Decision:** The `REQUIRE_DOCKER_TESTS` skip→fail hook lives in `tests/plugins/require_docker.py`, loaded from `tests/conftest.py` through `pytest_plugins`, not inline in `conftest.py`. A separate module is what `pytester` can load (`-p tests.plugins.require_docker`) to prove both directions. A `skipif` mark skips during setup, so under the hook it reports as an error, not a failure. Either way the run exits non-zero.
  **Issue:** #35
- **Decision:** Actions resolved 2026-10-24: `actions/checkout@v7` (v7.0.1), `astral-sh/setup-uv@v10.3.2` (exact, since there is no `v10` tag), uv `0.13.5` (= Dockerfile). `.github/rulesets/main-pr-required.json` is the Engine's file verbatim, because its `pull_request` parameters equal this repo's live ruleset.
  **Issue:** #35
- **Decision:** The `test` and `lint` jobs run their later checks with `if: always()`, so one failure doesn't hide the next. `manage.py check` and `makemigrations --check` run even if pytest fails.
  **Issue:** #35
- **Decision (Rule 3):** M0's `test_pull_matches_the_registry` required a "Skipped" line for the console's `pull_policy: build` image. Compose on GitHub's runners prints no line for that image, so the first CI run of the new gate failed on it (PR #101, run 40110247141). The test now accepts either output and still fails if Compose prints any other verb for the console tag.
  **Issue:** #35 (the gate it blocked), #32 (the test)
- **Decision (drift, Rule 3):** The engine-smoke Engine gets four `EPISTREL_*` variables, not two. Engine `edge` refuses to start without `EPISTREL_SERVICE_CREDENTIALS`, reproduced 2026-10-24 with the planned two-variable `docker run` (`ConfigError: at least one credential of 32+ characters is required`). It also gets `EPISTREL_CONFIG_KEY`. Both are the Engine compose's dev-only literals already in `docker-compose.yml` (M0 made the same call for #32). The smoke still calls only unauthenticated routes and sends no key. #42 (M2) still owns the Console sending one.
  **Issue:** #36, #42
- **Decision (Rule 1):** "Live smoke on" is `github.event_name == 'pull_request' || inputs.live_smoke`, not the planned `inputs.live_smoke == false && 'false' || 'true'`. On a PR `inputs` is empty, and Actions compares `null == false` as true, so the planned expression would have skipped the smoke, and accepted the skip, on every PR. The workflow test pins the form used.
  **Issue:** #36
- **Decision:** The `engine_live` usage error fires for a blank `ENGINE_LIVE_URL` too. `tests/engine_live/` has an `__init__.py` (repo convention, as for #34). `gate.sh` treats a missing job result as a failure.
  **Issue:** #36
- **Decision:** `compose.smoke.yml` publishes the Engine on 127.0.0.1:8000 as planned. The README says to stop the Engine repo's own stack first, because both use 8000. Otherwise the "runs alongside the Engine repo's stack" note would be untrue for this override.
  **Issue:** #36
- **Decision (Rule 3):** M0's `test_pull_matches_the_registry` also required a "Pulled" line for the Engine image. The runner's Compose prints none (PR #101, run 40110611891). The test now relies on its existing proof, that the local image's digest equals the registry's after the pull. The "never pull the console tag" check still fails on any line naming the console tag that isn't "Skipped".
  **Issue:** #37, #32
- **Decision:** The "base-file pull names all three images" check uses `docker compose config --images`, not the planned `docker compose pull --dry-run`. The dry run still contacts the registry and fails (403) until `epistrel-console:edge` exists, so it couldn't pass on this milestone's own PR.
  **Issue:** #37
- **Decision:** `publish-edge.yml` pins buildx's BuildKit image (`driver-opts: image=moby/buildkit:v0.34.1`, resolved 2026-10-24). setup-buildx's `docker-container` driver otherwise pulls a floating BuildKit at run time (the reference's floating-download rule; the Engine leaves it unpinned). `resolve` gets `packages: read` and logs in on dispatches, so the `sha-<7>` lookup works even while the package is private. Its result picks the path: the `retag` job, or `ci` → `publish`.
  **Issue:** #37
- **Decision:** On a failure, the `summary` job exits 1 after naming the stage, so a run that published nothing is red even when the failing job was skipped by a condition.
  **Issue:** #37
- **Decision (Rule 1):** The `validate` step in `release.yml` writes `tools.release validate` output to a file and fails with an `::error::` carrying the refusal. The Engine's version pipes it into `tee` under GitHub's default shell (`bash -e`, no `pipefail`), where a refused tag would pass the step. The Engine's own `release.yml` has the same pipe, and I didn't touch it; that's another repo, so it's out of scope here.
  **Issue:** #38
- **Decision:** The notes edit (append, or replace in place, the `Image:` and `Tested with Engine` lines) is `tools/release/notes.py`, run with plain `python3 -m` (stdlib only), not inline shell. That keeps it typed and unit-tested (`tests/tools/test_release_notes.py`). The CLI's Engine-image test puts a fake `docker` on `PATH`, so the code has no test-only lookup mode. `--engine-image-lookup` accepts only `docker`.
  **Issue:** #38
- **Decision:** `release.yml` reuses #37's pattern: push by digest, smoke, then `imagetools create` for every tag with a post-condition. That replaces the Engine's push-with-tags, so a failed smoke moves no tag (the AC). The release body goes through `--notes-file`.
  **Issue:** #38
- **Decision:** `rollback.yml` folds input validation and the running-release check into the one `id: pre` step, ahead of all existence checks. The Engine has them as three steps, one after its login. The guard test then checks a single, simple fact: nothing that runs `imagetools create` or `gh release edit` comes at or before `pre`. The job checks out the repo so it can run `python3 -m tools.release.notes --rollback-note`, which replaces an earlier note rather than skipping when one exists (the Engine's `grep -qF … ||` skips).
  **Issue:** #39
- **Decision:** `tools/drift_issue.py` accepts the bot author as any of `github-actions[bot]` (REST), `app/github-actions` (what `gh … --json author` returns for an app) or `github-actions`. The identify step in `engine-drift.yml` is `continue-on-error`, so an unresolvable digest is reported as `unknown` (planned) instead of failing a drift verdict. The tool reads the diff file only for exit code 1.
  **Issue:** #40
- **Decision:** `RELEASING.md` gains a short **Engine API drift check** section, with the `gh workflow enable` note for GitHub's 60-day schedule pause, and `.n8/config.yml` `ci.triggers` gains `schedule (daily) → Engine API drift issue` (both planned).
  **Issue:** #40
- **Decision:** The status views' test seam is `views.engine_state()`, which builds one client (or `None`) and calls `engine_status.probe_engine()`. It replaces the `views.probe` patch point #30's view tests used, so those tests changed only that line. The API check reads the probe's last state through a new `engine_probe.last_state()` and suppresses `unknown` while the probe says unreachable (planned). Its own log state sits behind a lock, because the two calls run on worker threads.
  **Issue:** #41
- **Decision:** The concurrency test was checked against a mutant (`max_workers=1`): it failed at 2.07 s against the 1.8 s bound, then the file was restored. Against the live Engine `edge`, the `xfail(strict=False)` API-match smoke test XPASSes.
  **Issue:** #41

## /n8-exec M1 (post-merge proofs) — 2026-10-24

- **Decision:** The milestone PR (#101) was opened as a draft right after #35, so the new CI ran on GitHub while the rest landed. That surfaced the runner Compose-output and `null == false` problems early. The body was rewritten with the `Closes` lines before merging. Only #34, #36 and #41 were in `Closes`. #35 and #37–#40 have post-merge acceptance criteria and were closed by hand once their proofs were recorded.
  **Issue:** #101
- **Decision:** The "inspect `edge` while logged out of ghcr" check used an empty `DOCKER_CONFIG` holding only a link to the CLI plugins, not `docker logout ghcr.io`. Logging out would have removed the owner's stored ghcr credentials on this machine. The package was already `public` after the first push, so `needs-owner-action` wasn't needed.
  **Issue:** #37
- **Incident:** While preparing the `n8/drift-probe` branch, `git commit -am` also committed the owner's uncommitted `.idea/epistrel-console.iml` edit, and that commit was pushed. Fixes: the owner's edit was restored to the working tree byte for byte (checked with `cmp`), the probe branch was force-pushed with only `console/engine/openapi.json`, and the branch was deleted after the proof. The IDE file's content sat on the remote for about a minute in a now-unreferenced commit.
  **Issue:** #40

## /n8-exec M1 (verification fixes) — 2026-10-24

- **Decision:** M1's PR #101 had already merged, so this run executed the six bugs `/n8-verify M1` filed (#105–#110) on a new branch, `milestone/m1-verify-fixes`, as was done for M0.
  **Issue:** #105–#110
- **Decision:** Workflow behaviour is now tested by running each step's `run:` script under `bash -e`, GitHub's default shell, via a new `tests/workflow_steps.py`. `gh` and `docker` are always stubbed, and an unanswered stub exits 99, so a test can never reach a real registry or release. Static rules (`tagging_bypasses`, `rebuild_steps`, the Engine-guard rule) cover what can't be run: step order, `if`, `continue-on-error`. Each rule and each behaviour test has a mutation shown to fail.
  **Why:** The issues asked for tests of what the steps *do* (refuse an off-main ref, demote with `--latest=false`, leave `X.Y` alone). Text checks against the YAML only prove the text is there.
  **Issue:** #105, #106, #107
- **Decision (Rule 3):** In `release.yml`, `"${flags[@]}"` became `${flags[@]+"${flags[@]}"}`. Under `set -u`, bash before 4.4 treats an empty array as unbound. The runner's bash 5 is fine, but macOS's `/bin/bash` 3.2 is too old, so the new stubbed-step test failed locally for a stable, highest release (no flags). Behaviour on the runner doesn't change. The Tag step also gained `id: tag`, so it shares edge's tagging rule.
  **Issue:** #106
- **Decision:** Rollback's good-image inspect now names the registry's own error ("cannot inspect … (image tag not found, or the registry refused): <stderr>") instead of always saying "image tag not found". The bad image's inspect still tolerates failure (`|| true`): a demoted version may legitimately have no image, so its tags just aren't moved.
  **Issue:** #107
- **Decision (Rule 2):** `_principal_header` mirrors the Engine's whole `requesting_principal` rule on the encoded value. It refuses whitespace-only principals and the reserved `system` as well as the over-length and control-character cases the issue named. C1 controls (U+0080–U+009F) are allowed: they're percent-encoded, so the Engine receives ASCII.
  **Why:** All of these are headers the Engine would refuse with 400 `principal_required`. A local `ValueError` is the issue's stated goal.
  **Issue:** #108
- **Decision:** The deadline fallback in `engine_status.probe_engine` now logs one WARNING per call that misses the 4 s deadline ("no answer from /openapi.json within 4.0 s; reported as timeout"). The abandoned call isn't cancelled, and its own later line (e.g. `transport error`) still follows. A real cancel would need an overall deadline inside the client, which is a bigger change than this low-severity finding warrants.
  **Issue:** #109
- **Decision:** `engine_schema` exits **4** on an unexpected exception, printing the traceback and "no verdict" on stderr. `tools.drift_issue` already passes any code other than 0/1 through without touching an issue, and its tests already include 4.
  **Issue:** #109
- **Decision:** The mypy guard is now `mypy_weakenings(ci, pyproject)` in `tests/test_workflows.py`, replacing `test_mypy_is_not_weakened_in_ci`. Beyond the issue's `--disable-error-code` and overrides with `ignore_errors`, it flags `--follow-imports…`, `--config-file…`, any `allow_*`/`ignore_*` turned on, and any `disallow_*`/`warn_*`/`check_*`/`strict_*` turned off.
  **Issue:** #110
- **Decision:** The README now says "Python 3.14 (pinned in `.python-version`…)". `pyproject.toml`'s `requires-python = ">=3.12"` is unchanged, because narrowing it changes the lockfile's resolution and the finding was about the README only. A test pins the README to `.python-version`.
  **Issue:** #110
- **Decision:** #105 and #110 share one commit (7f03c0f), because both edit `tests/test_workflows.py`.
  **Issue:** #105, #110

## /n8-exec M1 (second verification fixes) — 2026-10-24

- **Decision:** M1's PRs #101 and #111 had already merged, so this run executed the six bugs the second `/n8-verify M1` filed (#112–#117) on a new branch, `milestone/m1-verify-fixes-2`. Every mutation the issues listed was applied to the real file, the suite was run, and the file was restored and checked byte for byte. All of them now fail at least one test.
  **Issue:** #112–#117
- **Decision:** The smoke and Tag step tests are parametrized over `publish-edge.yml` and `release.yml`, because the two scripts are identical. That covers #113's D3 (the release smoke's `exit 1`) from `tests/test_workflows.py`.
  **Issue:** #112, #113
- **Decision:** publish-edge's short-SHA refusal is now case-insensitive and also refuses a hex prefix followed by `~`/`^` (`grep -Eiq '^[0-9a-f]{7,39}([~^].*)?$'`). A branch or tag named like a short SHA was already refused, so nothing legitimate is newly lost.
  **Issue:** #112
- **Decision:** In `rollback.yml`, an inspect error matching `not found|manifest unknown` (case-insensitive) counts as a missing tag. Any other inspect error stops the run and quotes the registry's stderr. The retag step now reads all three tags before moving any, using indexed arrays (bash 3.2-safe) rather than an associative array. Before, a refusal on `X.Y` would have come after `latest` had already moved. `RELEASING.md` lists the new refusal.
  **Why:** #115 asked for a missing bad tag to be fine and any other error to abort. Checking every tag first makes "abort before mutating" hold for the retag step too.
  **Issue:** #115
- **Decision:** #114 and #115 share one commit (a90c985), because both edit `rollback.yml`'s pre step and `tests/test_rollback_workflow.py`. Two of the first round's #114 mutations were wrong: `{ …; exit 1; } || true` still exits, so behaviour never changed. They were re-run as the issue states them (`>/dev/null || true` on the `gh release view` call) and were killed.
  **Issue:** #114, #115
- **Decision:** `[tool.mypy]` is now checked against an allow-list instead of prefix matches. Allowed: `strict = true`; any `disallow_*`/`warn_*`/`check_*`/`strict_*`/`extra_checks`/`local_partial_types` set to true; the full `files` list; `plugins == ["mypy_django_plugin.main"]`; `enable_error_code`; and `module` inside overrides. Anything else fails, including `# mypy:` inline comments and a whole-line `# type: ignore` in checked files. A `# type: ignore[code]` on a line of code stays allowed (strict's `warn_unused_ignores` checks it).
  **Issue:** #116
- **Decision:** `requires-python` changed from `>=3.12` to `>=3.14`, reversing the previous run's call to leave it. Re-locking removed only cp312/cp313/pp312 wheels and one `typing-extensions; python < '3.13'` marker. No package version changed. Ruff's target moved with it, so `console/engine/client.py` lost a quoted annotation (UP037) and `console/version.py` took PEP 758's unparenthesized `except` (Rule 3: the gate would fail otherwise). Every setup-uv step and both Dockerfile stages are now tested against `.python-version`, and so is every workflow's `UV_VERSION`.
  **Why:** #116 item 4 filed the mismatch. The lock change was checked to be wheel-only before committing.
  **Issue:** #116
- **Decision:** `tools.drift_issue` returns 4 ("no verdict", engine_schema's own code) for exit 1 with an empty or whitespace-only diff. The workflow's check step is unchanged. Fixing it in the tool keeps one place that decides what counts as drift.
  **Issue:** #117

## /n8-exec M1 (third verification fixes) — 2026-10-24

- **Decision:** M1's PRs #101, #111 and #118 had already merged, so this run executed the two bugs the third `/n8-verify M1` filed (#119, #120) on a new branch, `milestone/m1-verify-fixes-3`. Both are test-only: neither workflow nor config changed. Every mutation in #119, plus one for each minor item, was applied to the real `publish-edge.yml`, the suite was run, and the file was restored and checked byte for byte. Each mutation is killed by the test written for it.
  **Issue:** #119, #120
- **Decision:** The summary step is tested by running it under nine `needs.*.result` combinations: re-tag, publish, failed resolve, gate, build, smoke and tag, a cancelled publish, and a failed retag. The tests assert the exact summary lines and the exit code. The bite test counts a mutation as killed when any combination's (rc, summary) differs from the original's. It also mutates the stage precedence (gate/retag/resolve), beyond the issue's D3.
  **Issue:** #119
- **Decision:** The gha cache scope, the cleanup step's `if: always()`, and the smoke's exact `docker run` argv (including `-e CONSOLE_ENGINE_URL=http://127.0.0.1:9`) are asserted for both `publish-edge.yml` and `release.yml`, because the two jobs are identical.
  **Issue:** #119
- **Decision:** `MYPY_INLINE` now also matches a whole-line `# type: ignore` followed by `# …`. mypy 2.4.0 was checked to skip a file under both `# type: ignore  # legacy` and `#type:ignore#x`, but not under `# type: ignore[operator]` (that stays allowed). A new `mypy_config_shadows` refuses `mypy.ini`, `.mypy.ini`, and a `setup.cfg` with any `[mypy…` section. That covers the per-module `[mypy-pkg.*]` form too. User-level configs (`~/.mypy.ini`, `~/.config/mypy/config`) are outside the repo and not checked: the CI runner has none.
  **Issue:** #120
- **Decision:** #119 and #120 share one commit, because both edit `tests/test_workflows.py`.
  **Issue:** #119, #120

## /n8-exec M1 (fourth verification fixes) — 2026-10-24

- **Decision:** M1's PRs #101, #111, #118 and #121 had already merged, so this run executed the two bugs still open in M1 (#124, #122) on a new branch, `milestone/m1-verify-fixes-4`.
  **Issue:** #124, #122
- **Decision:** `engine-up.sh start` asks the image which settings it accepts. It runs `docker run --rm --entrypoint python IMAGE -c …`, which lists `epistrel.config.Settings.model_fields`, and passes only the entries of a fixed four-setting `ENGINE_SETTINGS` table that the image knows. The alternative was a version-keyed table in the script. I chose the probe because a table goes stale with each Engine release, and `edge` has no version to key on. A failed probe, or an image without `EPISTREL_DATABASE_URL`, stops the start; it never guesses. The cost is that the CI script now depends on the Engine's internal module path (`epistrel.config`, `ENV_PREFIX`, `Settings`). This is CI startup, not Console runtime, so it doesn't break the "public REST API only" rule. If the Engine moves that module, the smoke fails loudly with "could not read the settings".
  **Why:** #124. Engine 0.1.0/0.1.1 have only DATABASE_URL, LOG_LEVEL, HOST, PORT and DEBUG (checked in the Engine's tags and by probing the images). `edge` adds SERVICE_CREDENTIALS and CONFIG_KEY.
  **Issue:** #124
- **Decision:** I added a separate `engine-release-smoke` job in `ci.yml` rather than a matrix leg of `engine-smoke`. With a matrix, both legs would write the job outputs that `release.yml` reads to name the tested Engine, and the last leg to finish would win. The new job resolves the newest Engine release with `gh release view --repo nathanpond/Epistrel` (a public repo, so `github.token` is enough) and has no outputs. It runs under the same live-smoke condition as `engine-smoke`, so the gate treats both the same way: either may be skipped only when `LIVE_SMOKE=false`.
  **Issue:** #124
- **Decision:** Re-tagging `v0.1.0` at the fix commit is left to the owner. Deleting and re-pushing a release tag is outside this run's authority; the only irreversible step it may take is the merge.
  **Issue:** #124
- **Decision:** The mypy guard now scans every first-party `*.py`/`*.pyi` under the repo, pruning dot-directories, `__pycache__` and `node_modules`, instead of only `MYPY_FILES`. It refuses any `.pyi` file and any occurrence of the no-type-check decorator's name, a plain text match that catches every import spelling. The guard spells that name in two pieces so its own file passes the scan. A `getattr(typing, …)` alias would still get past it; deliberate obfuscation like that is out of scope.
  **Why:** #122. Every empty-code form (`[]`, `[ ]`, `[,]`, `[ , ]`, `[,,]`, `[\t]`, `ignore []`, with a trailing `# why`) and the BOM case were checked against real mypy 2.4 to skip the file. `[misc]` and `[-]` do not skip it and stay allowed.
  **Issue:** #122

## /n8-exec M2 — 2026-10-24

- **Decision (drift, dependency graph):** #42's native `blocked_by` edge to Console #44 is ignored. #42's body says the dependency is on **Engine** nathanpond/Epistrel#44 (the service credential, long on `edge`); the native edge points at the same number in this repo, which would make #42 → #44 → #43 → #42 a cycle. Execution order: #42, #43, #44, #45, #46, #47, #48, #49, #50, #51, #52, #53, #54.
  **Why:** The body is explicit; the edge is a cross-repo number copied into the wrong repo.
  **Issue:** #42
- **Decision:** Each story is implemented by a fresh subagent working sequentially on `milestone/m2-lite-core-console`, from a shared brief; the orchestrator re-checks each story's tests, commit and issue comments before starting the next.
  **Why:** Thirteen stories with 9–24 KB plans each; a fresh context per story keeps every plan line in view.
  **Issue:** M2
- **Decision (drift):** The engine-wide guard check ran once (Engine `edge` = 3d4c7f4, digest `sha256:cf26dc0b…`). A bare `docker run --rm ghcr.io/nathanpond/epistrel:edge` exits 1, but it names `EPISTREL_DATABASE_URL: is required`, the first setting the Engine checks, not the credential. With only a DB URL set it exits 1 with `EPISTREL_SERVICE_CREDENTIALS: at least one credential of 32+ characters is required`. So Engine #44 is enforced on `edge`. The compose `EPISTREL_CONFIG_KEY` literal matches the Engine compose at that commit.
  **Why:** The planned guard assumed the credential is the first error; the second run isolates it.
  **Issue:** #42
- **Decision (drift):** Compose already had the Engine credential, config key and both volumes (M1 Rule 3 fix). #42 adds the console's `CONSOLE_ENGINE_SERVICE_KEY` and the `# dev-only default — override for anything but local use` comment on all three literal lines. The story's test files map as named: `tests/test_workflows.py`, `tests/test_startup_refusal.py`, `tests/test_engine_config.py`, `tests/container/test_compose.py`. The CI credential's own tests are in `tests/test_engine_up_script.py`, where #124 put engine-up.sh's fake-docker tests.
  **Why:** The files exist under those names; nothing was renamed.
  **Issue:** #42
- **Decision:** Error order stays as the code had it: `CONSOLE_ENGINE_URL`, then `CONSOLE_ENGINE_SERVICE_KEY`, then unknown `CONSOLE_*` variables. The planner wrote "unknown first (#29's existing order)", but the existing code and test put the URL first. The parenthetical's intent (keep #29's order) wins, and the key goes straight after the URL.
  **Why:** The two parts of the planner line contradict each other. Keeping the shipped order changes no existing behaviour.
  **Issue:** #42
- **Decision:** The shared serving-test key `test-service-key-not-a-secret-000000000` is `SERVICE_KEY` in `tests/helpers.py`, not a conftest constant.
  **Why:** Tests already import their subprocess helpers (`scrubbed_env`, `REPO`) from there; importing from a conftest is discouraged by pytest.
  **Issue:** #42
- **Decision (Rule 3):** The image smokes in `publish-edge.yml` and `release.yml` now `docker run` the Console with `-e CONSOLE_ENGINE_SERVICE_KEY=smoke-only-key-not-a-secret`. `tests/container/test_image.py` passes the shared test key and gains a no-key refusal case.
  **Why:** With the key required, gunicorn `--preload` refuses to start and every publish would fail its smoke. The key is never used: those smokes reach no Engine.
  **Issue:** #42
- **Decision (Rule 3):** The new keyed `GET /stories` live smoke is marked `engine_auth` (a new marker). `engine-release-smoke` runs `pytest -m "engine_live and not engine_auth"`, and the edge `engine-smoke` still runs every live test.
  **Why:** The newest Engine release, v0.1.1, has only `/health` (checked in the image) and no service auth, so the test would fail the PR gate there. A release whose tracked Engine lacks it still runs `engine_auth` through ci.yml's engine-smoke, which is correct: an M2 Console needs those routes. Later stories adding M2-route live tests should use the same marker.
  **Issue:** #42
- **Decision:** `engine-up.sh start` writes `ENGINE_LIVE_SERVICE_KEY` to `$GITHUB_ENV` after a successful `docker run` even for a released Engine that does not accept `EPISTREL_SERVICE_CREDENTIALS`. An empty `ENGINE_LIVE_SERVICE_KEY` is a usage error, but a whitespace-only one is not trimmed, matching the Console key.
  **Why:** The release smoke must not hit a UsageError, and an Engine without auth ignores the header.
  **Issue:** #42
- **Decision:** Login comes from #43, which is not built yet. The compose and live tests load `/` without signing in, and the planner's "sign in first" note applies once #43 lands.
  **Why:** #42 runs before #43 (execution order above).
  **Issue:** #42
- **Decision (drift):** No schema re-record for #43. `engine_schema --check` against a fresh Engine `edge` (published on 127.0.0.1:18000, torn down after) reports a match, and the recorded `HealthResponse` / `HealthChecks` / `WorkersCheck` already carry the M2+ fields. The models stay as the schema says (`content_logging: bool = False`, `database` and `ffmpeg`/`ffprobe` as enums, `workers` defaulting to `{}`), not the planner's looser `bool | None` / `str | None`; the probe reads `content_logging` from the raw body, so `/health` JSON still carries `null` when an M0 Engine omits it.
  **Why:** The planner line says the fields must match the re-recorded schema, and the contract test enforces it.
  **Issue:** #43
- **Decision (drift):** `StorySummary` mirrors `StorySummaryBody` in full (id, name `str | None`, owner_principal, status as the four `StoryStatus` values, timeline_revision, next_turn_index, content_rating as `StoryRating`, created_at, archived_at). It has no `characters`: the schema's list item has none. `list_stories(status=None)` returns `list[StorySummary]` through a new `StoryList` model.
  **Why:** The contract test requires a model's fields to equal the component's properties; #45 gets characters from `GET /stories/{id}`.
  **Issue:** #43
- **Decision (Rule 3):** `tests/engine/contract.py` `check_operation` no longer compares the response model with responses that reference the shared `Error` envelope (401/422/503 on `/stories`). Those become `EngineError`, not the model. `/health`'s 503 still references `HealthResponse` and is still checked.
  **Why:** `GET /stories` is the first registered operation with error responses; without this every Engine route would fail the contract.
  **Issue:** #43
- **Decision:** Operators are active superusers everywhere: the login form, the admin (`OperatorAdminSite.has_permission` and its `login_form`, installed via `console.admin_config.OperatorAdminConfig`), and `engine_client_for` (`console/operators.py:is_operator`). The planner's `is_staff` check for `engine_client_for` is tightened to match the round-two "only superuser" convention.
  **Why:** One rule in one place; a staff-only account must not reach the Engine by any route.
  **Issue:** #43
- **Decision (drift):** Anonymous `/admin/` redirects to `/admin/login/?next=/admin/`, not `/accounts/login/`. Django's admin views are exempt from `LoginRequiredMiddleware` and send visitors to their own login, which now uses the operator form and refuses staff-only accounts with the same message.
  **Why:** That is Django 6.1's behaviour; the security property (no staff-only bypass) holds and is tested.
  **Issue:** #43
- **Decision:** The status page's three reads run in `console/engine_status.py:status_reads(client)` on #41's pool under the one 4 s deadline; `/health` keeps `probe_engine(client)` (two reads, no key check). The key line is `Service key: accepted` / `rejected (401)` / `not checked — Engine unreachable` / `not checked — Engine not configured`, and any other answer (e.g. 503, bad body, deadline) reads `unknown — …` rather than claiming the key is wrong. When no key is configured the line adds `(no service key configured)`.
  **Why:** Only a 401 proves the key wrong; the old "Service key: set / not set" line is replaced by the verdict.
  **Issue:** #43
- **Decision:** Degraded reasons: `no worker alive` when `alive == 0`, else `no worker heartbeat` when `last_heartbeat_s` is null; `worker heartbeat N s ago` whenever the heartbeat is above 60 s (so `alive 0` with a 75 s heartbeat gives both); overdue values and the heartbeat are compared strictly above their thresholds; `erasure_pending` only above zero.
  **Why:** The planner's order lists the two worker reasons as alternatives and the heartbeat age as its own reason.
  **Issue:** #43
- **Decision:** The badge is `console/context_processors.py:engine_badge` (a `SimpleLazyObject` over `badge_probe(request)`); views hand their own probe over with `remember_probe(request, result)`. A second processor, `shell`, supplies the nav items (active by path prefix) and `console_version` for the footer. Tests get an autouse `badge_probe_calls` fixture that stubs the badge's own probe (no test reaches a real Engine from the shell) and records its calls.
  **Why:** Keeps the nav and version out of every view; the stub keeps tests offline whatever the developer exports.
  **Issue:** #43
- **Decision (drift):** htmx 4.0.0 (latest release, 2026-08-28; sha256 `e484d917…593f`, identical to npm's `htmx.org@4.0.0`) is vendored at `console/static/console/htmx.min.js`. In htmx 4, `hx-disabled-elt` is renamed `hx-disable` (htmx 2's `hx-disable` is now `hx-ignore`), attribute inheritance needs the `:inherited` suffix, and every non-204/304 response is swapped. Later stories use `hx-disable="this"` with `hx-sync="this:drop"`. `base.html` sets `hx-headers:inherited` with the CSRF token on `<body>`.
  **Why:** Checked with context7 against the v4.0.0 docs; the planner's `hx-disabled-elt` is the htmx 2 name.
  **Issue:** #43
- **Decision:** Error-panel log line for non-HTTP errors: `engine error - unreachable (<reason>) <method> <path>` and `engine error - not_configured <method> <path>`; the method and path are the Console request's. Detail keys matching #44's redaction names (`api_key`, `key`, `secret`, `password`, `credential`, `service_key`, `authorization`, case-insensitive, any depth) show `***`; the list and `redact()` live in `console/redaction.py` for #44 to reuse.
  **Why:** `EngineError` carries no Engine path; one redaction list serves both stories.
  **Issue:** #43
- **Decision:** `apply_engine_errors` (`console/engine_form_errors.py`) is not built in #43; the first story with an Engine-backed form builds it (expected #45's new-story form or #46).
  **Why:** No form exists yet to test it against, and the `details.errors[]` shape is best fixed against that story's real 422 fixture.
  **Issue:** #43
- **Decision:** `python-ulid>=4.0.1` (latest on PyPI, 2026-07-20) is the ULID library; `console/ids.py:new_ulid()` returns `str(ULID())`.
  **Why:** The convention names python-ulid; version resolved at authoring time.
  **Issue:** #43
- **Decision (drift):** #43 lands as two commits. A staging error (a pathspec for the already-moved `console/views.py`) made the first commit, `ed195bd`, carry only the `console/views.py` → `console/views/status.py` rename under the full story message; the second commit carries the rest. History was not rewritten (the brief forbids it); the pair is the story.
  **Why:** Amending would rewrite history, which the run's rules forbid even before a push.
  **Issue:** #43
- **Decision:** The `EngineOperation` table (`console/models.py`, migration `0001_engine_operation`) is planned scope, not a Rule 4 surprise: #44's AC and must-haves prescribe it, and its discretion classes it as the "saved debug session" data CLAUDE.md allows.
  **Why:** The plan names the model, its columns and the migration explicitly.
  **Issue:** #44
- **Decision (drift):** Four pass-2 discretion lines in #44 ("Column types:", "Error and failure rows:", "Queued turns:", "List page:") are empty headings; their sub-bullets are missing from every revision of the issue and from the planner's notes. The executor filled those areas from the round-one lines and the AC, as logged below.
  **Why:** No source holds the lost text; the remaining lines cover each area.
  **Issue:** #44
- **Decision:** `@operation` now wraps each client method so `console.engine.client.current_call()` returns a `Call(kind=<method name>, operation=<registration>)` while it runs (`calling(call)` sets it by hand). `RecordingEngineClient(EngineClient)` in `console/engine_access.py` overrides only `_request`, reads that context, and records when the registration's method is POST/PUT/PATCH/DELETE. A bare `_request` call outside any registered method goes by its own HTTP method and gets kind `"<METHOD> <path>"`.
  **Why:** The planner's "override the single internal request method" needs the method name and registration there; a context var gives both without a hand-kept list, so a new `@operation` method is recorded with no extra code.
  **Issue:** #44
- **Decision:** `_request` gains `json=` (the body) and `headers=` (extra request headers; `User-Agent`, `Accept`, `Authorization` and `X-Epistrel-Principal` can never be overridden through it). `EngineClient.from_settings()` returns `Self`. `console.engine.client.EngineResponse` aliases `httpx.Response`, so the recorder names the type without importing httpx (boundary rule).
  **Why:** Mutating methods (#45 on) need a body and the idempotency/revision/Prefer headers; the recorder needs the response type.
  **Issue:** #44
- **Decision:** Row logic lives in `console/operation_log.py` (`Observed`, `row_fields`, `record`, `complete_queued`, plus the readers `turn_report_json`, `is_turn`, `input_violations`, `output_violations`, `policy_ids`, `is_guardrail_rejection`). `console.engine_access.complete_queued(engine_operation_id, op_record)` is #48's entry point.
  **Why:** Keeps the recorder small and the guardrail readers shared by the views and #48.
  **Issue:** #44
- **Decision:** Outcome rules: 202 with a JSON object → `queued`; other 2xx → `ok` when it is a replay (`Idempotent-Replay: true` or a body with `reference`), a 204, a call with no response model, or a body that parses as the registration's response model; else `error`/`bad_response`. Non-2xx → `error` with the envelope's code, else `http_<status>`. The model check is skipped for 202, because a method's response model describes its 200.
  **Why:** The planner's "parse failure is bad_response" applied inside `_request`, where the registration's model is known.
  **Issue:** #44
- **Decision (drift):** A turn's `RespondResponse` has no top-level `operation_id`, so the generic extractor also takes the first `beats[].operation_id` (after top-level `operation_id` and the ingest batch's `results[0].operation_id`). Without it no turn row could offer "Fetch from Engine" (demo step 4).
  **Why:** The recorded schema puts the turn's operation id only on its beats.
  **Issue:** #44
- **Decision (drift):** Absent text values (`story_id`, `engine_operation_id`, `idempotency_key`, `error_code`) are stored as `""`, not `NULL`. Numbers and JSON stay nullable (`http_status`, `response`, revisions, `elapsed_ms`). `story_id` and `engine_operation_id` are kept only when they fit 26 characters.
  **Why:** ruff's DJ001 (enabled in this repo) forbids `null=True` on CharFields and the brief forbids suppressing it; `""` is Django's convention for "no value" in text columns. An over-long path id (unknown ids pass through to the Engine) is not a ULID and stays visible in `path`.
  **Issue:** #44
- **Decision:** Every `queued` row lists as "queued (unconfirmed)", and `complete_queued` settles a row only on `committed` (→ `ok`, response replaced by the stored result, redacted, when present) or `failed`/`abandoned` (→ `error`, code from the stored result's error envelope, else the status).
  **Why:** The Console cannot tell a cutoff-hit row from one still polling without another column; the AC's label fits both.
  **Issue:** #44
- **Decision:** `OperationRecord` mirrors `OperationBody` in full; `result.body`, `creation_report`, `eval_hooks` and `read_set` are raw JSON (`Any`). The contract mapping (`tests/engine/contract.py`) learns `Any` ↔ any schema and `dict[str, Any]` ↔ `additionalProperties: true`. `TurnReport` has no component of its own; a new contract test checks each of its fields against `RespondResponse` and `RegeneratedBody`. Only `model_calls` and `elapsed_ms` are required, so it also reads a 422/503 `details`.
  **Why:** The planner keeps `result` raw; the contract test needs every `OperationBody` property typed.
  **Issue:** #44
- **Decision:** The "Fetch from Engine" result is shown redacted (the same `redact()` as stored rows), and the Fetch panel also shows kind, timeline revision and start/commit times beside the AC's status, principal, `config_revisions` and `result`.
  **Why:** A stored result could echo a secret; the extra fields cost nothing.
  **Issue:** #44
- **Decision:** The operator filter matches `operator_username` (the snapshot), so rows of a deleted user stay filterable. The guardrail readers treat the input side as `error.details.violations` and the output side as the turn report's `guardrail.violations`; the policy select lists ids from both.
  **Why:** Attribution must survive user deletion; the AC's policy filter covers both kinds of rejection.
  **Issue:** #44
- **Decision (drift):** No schema re-record for #45. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, torn down after) reports a match; every endpoint #45 needs (`POST /stories`, `GET /stories/{id}`, archive/unarchive, `DELETE /stories/{id}`, `GET /admin/content-ratings`, `GET /admin/settings`) is in the recording.
  **Why:** The conventions re-record only when the live document differs.
  **Issue:** #45
- **Decision:** New models mirror their components in full: `Story` (`StoryBody`), `StoryCharacter` / `OmniscientStoryCharacter`, `CatalogSource`, `StoryPause` / `StoryPauseProgress`, `DormantChain`, `VariantChain`, `ChainRef`, `ScenarioOrigin`, `StoryCreated`, `CreatedCharacter`, `StoryStatusChange` (`StatusBody`), `ContentRatingSummary` (`RatingBody`, #49's name), `ContentRatingList` (`RatingList`), `DeploymentSettings` (`SettingsBody`). Raw JSON (`Any`) where the contract test cannot express the schema: `Story.characters` (a union of two character bodies; `Story.cast` parses them as `StoryCharacter`), `StoryCreated.report`/`impact` (scenario-only), `DeploymentSettings.profile_defaults` (nested profile document, #50 renders it) and `feedback_retention` (enum-or-integer union).
  **Why:** The contract test supports only `X | None` unions; everything else is typed so #48/#50 can reuse the models.
  **Issue:** #45
- **Decision:** `create_story(spec, *, idempotency_key)` treats any non-201 success or a null `story_id` as `bad_response`; mutations (`create_story`, `archive_story`, `unarchive_story`, `delete_story`) use `MUTATION_TIMEOUT_S = 10.0` (new constant in `console/engine/client.py`). `list_content_ratings(include_retired=True)` sends `include_retired=1` (checked live: the Engine accepts `1`).
  **Why:** #43's timeouts and paging conventions; the planner's "a 200 or null id is bad_response".
  **Issue:** #45
- **Decision:** `apply_engine_errors` (`console/engine_form_errors.py`) strips the Engine's `body.` prefix (paths look like `body.characters[1].name`, checked live and in Engine `core/errors.py:field_path`), takes `formsets` as name → the forms **in the order sent** (so dropped blank rows do not shift indexes), puts a row path with no matching field on that row's non-field errors, and an unmatched path on the form's non-field errors prefixed `<path>: `. `aliases` keys may be a path, a `details` key or an error code (the latter two only when the error carries no field path). It returns how many errors it placed.
  **Why:** The convention fixes the mapping but not these edges; prefixing unmatched errors with the path tells the operator what they refer to.
  **Issue:** #45
- **Decision:** The shared confirm pattern is `console/confirm.py`: `ConfirmPage(title, question, confirm_label, cancel_url, emphasis, lines, facts, hidden, danger, fields_template)`, `confirm_action(request, page, perform)` (GET renders with zero Engine calls, POST calls `perform(idempotency_key)`, other methods 405), `render_confirm`, `safe_next(request, fallback)` and `posted_idempotency_key(request)` (the posted key, or a fresh ULID when missing or not matching the Engine's key rule). Templates: `console/confirm_page.html` around the shared `console/_confirm.html`; `{name}` in the question is replaced by the italic `emphasis`.
  **Why:** #53/#54/#46/#49 reuse it; `fields_template` covers #53's regenerate parameters without another template layer.
  **Issue:** #45
- **Decision:** The new-story page carries its rating options in a signed hidden field (`rating_choices`, `django.core.signing`), so a POST (create, add, remove, or a Console validation failure) makes no Engine read; a missing or tampered value is re-fetched. The GET reads `list_content_ratings()` and `get_deployment_settings()` and preselects `default_content_rating_id`.
  **Why:** The test plan asserts zero Engine requests for input the form refuses; the select still needs its options to re-render.
  **Issue:** #45
- **Decision:** Add/remove re-render a bound formset built from the posted rows (renumbered, `INITIAL_FORMS=0`) with errors hidden, so the new blank row is not flagged; a crafted add at 20 rows does nothing. A visually hidden "Create story" submit comes first in the form so Enter creates rather than removes row 1. `is_player` is always sent (explicit `false`); blank safe-fallback and deflection texts, name and viewpoint are omitted.
  **Why:** No JS (AC); Enter's default button is the first submit in the form.
  **Issue:** #45
- **Decision:** Any `EngineClientError` from the create POST re-renders the form with input kept: a 422 other than `guardrail_violation` places errors inline plus `The Engine rejected the story: <message>`; a guardrail violation and every other error show the shared panel above the form. Confirm-page POST errors escape to the middleware panel.
  **Why:** #43: form views that catch the error re-render their form; the planner's 422/guardrail split.
  **Issue:** #45
- **Decision:** A story without a name is named by its id in confirm wording and flashes ("Archive *<id>*?", "Deleted <id>"); the list and detail show "—". Flashes: `Created|Archived|Unarchived <label> — … revision N, idempotency key K` and `Deleted <label>`. Delete always returns to `/stories/` (its `next` may be the deleted story's page); Cancel uses `next`.
  **Why:** A flash of "—" names nothing; the Engine's answers carry revision and key (#43 flash convention), delete's 204 carries neither.
  **Issue:** #45
- **Decision:** The list's cast size comes from one `GET /stories/{id}` per row (sequential, per the planner); a failed per-row read shows "—" for that row only and logs the usual WARNING line. A held (`blocked_by_erasure`) row shows Delete only.
  **Why:** `StorySummaryBody` has no cast; per-section degradation (#43).
  **Issue:** #45
- **Decision:** URLs are `/stories/new/` (trailing slash, #43's "URLs end with `/`") and `/stories/<id>/{archive,unarchive,delete}/` in namespace `stories` (`list`, `new`, `detail`, `archive`, `unarchive`, `delete`). `tests/test_shell.py`'s placeholder test drops `/stories/`, which is no longer a placeholder.
  **Why:** Conventions; the placeholder was replaced as #43 planned.
  **Issue:** #45
- **Decision:** Two live smokes (`engine_live` + `engine_auth`): create → archive → list (archived present, cast 2) → delete in `try/finally`, and demo step 3's duplicate-name refusal placed on row 2 (creates nothing).
  **Why:** The planner's pass-2 smoke, plus the one 422 path a fake cannot prove matches the real Engine.
  **Issue:** #45
- **Decision (drift):** No schema re-record for #46. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, torn down after; `info.version` "m3") reports a match, and every model-config endpoint is in the recording. The parent map (`lite_summary` → `consolidation`, `rating_suggestion` → `guardrail_check`, `regeneration` → `generation`) was confirmed from the edge image's `parent_of`.
  **Why:** The conventions re-record only when the live document differs; the GET does not name the parent, so the Console mirrors the Engine's map (`console/model_config.py:PARENTS`).
  **Issue:** #46
- **Decision (drift):** Wire models mirror the schema components (`ModelConfig` = `ModelConfigBody`, `ModelConfigEntry`, `ModelConfigItem`, `ModelConfigMutation`, `ConfigSources` = `Sources`), not the planner's `ModelConfigList`/`PurposeConfig(Sourced…)` shape; that shape lives view-side in `console/model_config.py` (`PurposeConfig`, `Sourced`, built by `purpose_config`/`purposes`). `api_key` stays `str | None` (the mask) with an `api_key_set` property; `params` is `dict[str, Any]`.
  **Why:** The contract test compares each response model field-for-field with its component; the schema's `const "***"` is not an enum, so a `Literal` would not compare.
  **Issue:** #46
- **Decision:** A param no row sets (absent from `sources.params`) shows "— (not set)" marked `default`; its edit placeholder reads "default: — (not set)". Values on the list are shown as returned (`stop` comma-joined); the `default` row has no markers.
  **Why:** The Engine sends only keys some row sets; planner's "an inherited null shows default: — (not set)".
  **Issue:** #46
- **Decision:** The edit and key forms carry the purpose as read in a signed hidden `snapshot` (`django.core.signing`), so a re-render after a Console refusal or an Engine error needs no Engine read; a missing, tampered or other-purpose snapshot is re-read with the single-item GET. Clear-key's `config_key_missing` re-reads the purpose to render the edit page with the message.
  **Why:** #45's signed-hidden pattern; the planner's "input they reject makes zero Engine calls".
  **Issue:** #46
- **Decision:** `PUT` body: on a non-default purpose a blank field is absent and `params` is sent only when one param is given (all blank → `{}`); on `default` `base_url`/`model` and all six params are always sent, blank as `null`. `base_url` is validated with `epistrel_console.engine_config.parse_url` (#29's parser) but sent as typed (trimmed), not normalised.
  **Why:** AC "blank fields absent", planner "on default a blank field is sent as null"; normalising would change what the operator entered.
  **Issue:** #46
- **Decision:** Wording where the planner left gaps: drop confirm "Drop *p*'s override? Every field falls back to its parent purpose (parent) or default." (or "… falls back to default." with no parent) plus the per-purpose-key line; drop flash "<p> now uses default — config revision N, idempotency key K" (or "now uses <parent> or default"); 204 flashes "Nothing to drop — <p> has no override"; save flash "Saved <p> — config revision N, idempotency key K"; clear-key on `default` says "Purposes without a key of their own will have none.", on a child purpose names its parent's key. The api-key URL answers 405 to GET.
  **Why:** The confirm helper italicises one name; a child purpose falls back to its parent first; #43's flash names the revision and key.
  **Issue:** #46
- **Decision:** A key-PUT 422 is placed on the key field (re-rendered empty: `PasswordInput(render_value=False)`); `config_key_missing` (set or clear) renders the message inline above the key form; any other key error shows the shared panel above the key form; a config-PUT 422 places field errors (`params.<name>` and `params.stop[i]` aliased to the fields) plus "The Engine rejected the configuration: <message>", other errors show the panel above the form. Drop/clear confirm POST errors (e.g. `default`'s 409) go to the middleware panel.
  **Why:** #43's form-error and panel conventions; the planner's inline `config_key_missing`.
  **Issue:** #46
- **Decision:** Added an `engine_live` + `engine_auth` smoke (`test_model_purpose_edit_key_and_drop_live`): edit `quality_judge`, set its key, see "own" and the fingerprint without the key on the page, drop the override in `finally`.
  **Why:** Proves the PUT/key/drop bodies against the real Engine, which a fake cannot.
  **Issue:** #46
- **Decision (drift):** `console/forms.py` became the package `console/forms/`: `stories.py` (#45's forms), `model_config.py` (#46's) and the new `policy.py`; `console/forms/__init__.py` re-exports every story and model-config name, so `from console.forms import …` keeps working. Policy forms are imported from `console.forms.policy`.
  **Why:** #43's convention puts the shared policy forms in `console/forms/policy.py`, which needs a package; splitting by area keeps each module to one story's forms.
  **Issue:** #47
- **Decision (drift):** No schema re-record for #47. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, torn down after) reports a match. The Engine answers an edit of a retired policy with HTTP 409, code `validation_error` and `details.reason = "retired"` (checked live and in Engine `ConfigConflict`), while the schema lists `config_conflict`; the Console treats "409 with `details.reason == "retired"`" as the retired refusal ("Retired policies cannot be changed", input kept), and every other 409 goes to the error panel.
  **Why:** The planner's "a 409 with a retired-state code"; the reason, not the code, is what marks it.
  **Issue:** #47
- **Decision:** Models mirror `PolicyBody` (`Policy`), `PolicyList` and `PolicyMutation` in full; `rule` is `dict[str, Any]`, `age_condition` is `str | None` (the bodies type it as a plain string). The Literals `PolicyScope`, `PolicyKind`, `PolicyTarget` and `AgeCondition` live in `console/engine/models.py`; the contract test checks the scope enum through the `scope` property's `$ref` (bodies and the list query), `PolicyTarget` against `PolicyReplacement.applies_to`, and `AgeCondition` against the `const` of `PolicyFields`, `PolicyReplacement` and `StoryPolicyItem`.
  **Why:** The contract test compares models field-for-field; the planner keeps `AgeCondition` a checked Literal and finds the renamed scope component by `$ref`.
  **Issue:** #47
- **Decision:** The shared form for #54 is `PolicyContentForm(data, rule_types=…, kept_rule=…)`: `content()` is the `PolicyReplacement` body (targets in the Engine's order, `age_condition` null for "none", `effect: "deny"` always); `STORY_RULE_TYPES` adds `entity_list` with a `names` input (blank allowed: the story's characters). A rule whose type is not offered is kept read-only and resent unchanged. `PolicyCreateForm` adds the optional id (no format check, per the planner) and the combined scope select (`global` / `rating:<id>`; a bare `rating` is refused with "A rating-scoped policy needs a rating: choose one."). 422 paths go through `POLICY_ERROR_PATHS`, a mapping that sends `rule.<type>.<input>[i]`, `rule`, `applies_to[i]` and `scope_id` to the inputs shown (the Engine puts the rule type in the path, e.g. `body.rule.wordlist.words`).
  **Why:** #54 adds story and character scope to the same content fields; the live Engine's 422 paths name the rule type.
  **Issue:** #47
- **Decision:** On edit the rule-type select offers only types of the policy's kind (deterministic: wordlist, regex; model-assisted: model-assisted), so a forged change of kind is refused with zero calls. `applies_to` needs at least one target on create too (the schema's `minItems: 1`), and the new form preselects `text_input` and `text_output`, the Engine's default when `applies_to` is omitted (checked live).
  **Why:** The planner fixes kind after create while the rule type sets kind; sending an explicit target list keeps the form honest about what it sends.
  **Issue:** #47
- **Decision:** The list shows every scope the Engine returns (filter: All/Global/Rating/Story/Character); story and character policies have no Edit or Retire ("written through the story's settings", #54), since the Engine refuses them here (409 `story_scoped`). Sort: global, rating (by the rating's display order; unknown ratings last), story, character, then name (case-folded); Console-side paging at 50 per page. Ratings are read once per page; a failed read degrades to raw ids and, on the new form, no rating options plus a note. A 503 `erasure_pending` on a policy page renders the shared error panel (#64's held message does not exist yet).
  **Why:** AC "every policy"; #43's paging rule for Console-side lists; per-section degradation.
  **Issue:** #47
- **Decision:** `confirm_query(name, next_url)` and `shown_name(request)` moved from `console/views/stories.py` into `console/confirm.py` (stories now import them); the retire confirm uses them. Flashes: "Created|Saved <name> — version N, config revision R, idempotency key K", "Retired <name> — config revision R, idempotency key K", and "<name> was already retired — nothing changed" for the 204. The minors confirm button is "Retire (minors protection)", styled as dangerous, with the warning line.
  **Why:** One confirm-link helper for every story's action links; #43's flash convention.
  **Issue:** #47
- **Decision:** The Guardrails nav still points at `/guardrails/ratings/` (#49's placeholder); `placeholder(title, links=…)` gained optional links, and the ratings placeholder links "Guardrail policies". The policies list links "Content ratings" back.
  **Why:** Keeps #43's nav (and its test) while making the policies page reachable before #49 replaces the placeholder.
  **Issue:** #47
- **Decision:** Fixtures: `list_policies.json` (seeded minors policy as the live Engine returns it, a Teen wordlist policy, a retired global regex policy), `create_policy.json`, and `list_content_ratings_all.json` (the four seeded ratings as `?include_retired=1` returns them; #45's three-item fixture is untouched). Added an `engine_live` + `engine_auth` smoke: create a global wordlist policy, edit it (version 2), retire it, then an edit shows "Retired policies cannot be changed".
  **Why:** The planner's fixture list; the live smoke proves the bodies and the retired refusal against the real Engine.
  **Issue:** #47
- **Decision (drift):** #47 lands as two commits, like #43. A staging error (a pathspec for the already-moved `console/forms.py` aborted the whole `git add`) made the first commit, `5ed940d`, carry only the `console/forms.py` → `console/forms/stories.py` rename under the full story message, and it was pushed; the second commit carries the rest. At `5ed940d` alone `console.forms` has no `__init__.py`, so that commit's CI run fails; the second commit's is the one that counts. History was not rewritten.
  **Why:** The brief forbids rewriting history and force-pushing.
  **Issue:** #47
- **Decision (drift):** No schema re-record for #48. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, `EPISTREL_MODEL_ADAPTER=stub`, torn down after) reports a match; `respond`, `history`, `GET /stories/{id}/settings` and `GET /operations/{id}` are all in the recording.
  **Why:** The conventions re-record only when the live document differs.
  **Issue:** #48
- **Decision:** Client: `respond(story_id, body, *, idempotency_key, prefer_async=True, timeout)` → `RespondResponse | QueuedTurn`, `get_history(story_id, *, viewpoint, limit, before_turn)` → `History`, `get_story_settings(story_id)` → `StorySettings`, and `turn_timeout(deadline_s)` (deadline, else 45 s, plus 15 s). `@operation` gained `status_models={202: QueuedTurn}` (stored on `Operation.status_models`), and the contract check compares each such status against its own model; `EngineError` gained `retry_after` from the `Retry-After` header.
  **Why:** Respond's 202 references `QueuedTurn`, so the existing one-model-per-operation contract check could not register it; the 423/503 wording needs `Retry-After`.
  **Issue:** #48
- **Decision:** Models mirror the components in full (`RespondResponse`, `MessageBody` as `ReplyMessage`, `QueuedTurn`, `HistoryResponse` as `History`, `HistoryItemModel` as `HistoryItem`, `TurnSummaryModel`, `TurnMessageModel`, `StorySettingsBody`, `EffectiveBudgetBody`, `GuardrailCallsBody`). `beats`/`items`/`audit` stay raw (`list[Any]`, the schema's item is an omniscient/character union) and parse through `.entries`; `RespondResponse.report` builds #44's `TurnReport`; story policies stay raw for #54.
  **Why:** #43's frozen-dataclass convention and the field-for-field contract test; #45's `Story.characters` set the union pattern.
  **Issue:** #48
- **Decision (drift):** `deadline_s` is bounded 0.5–600 and `max_calls` 1–50 (the recorded `BudgetOverride`), not the planner's min 0.1; degradation causes come from `degradation_details[].cause` (`DegradationBody`), not `details.cause`, and the existing `_turn_report.html` already shows them.
  **Why:** #43: forms copy the bounds the schema states; the planner asked for the cause path to be re-checked against the schema.
  **Issue:** #48
- **Decision:** The play view lives in `console/views/play.py` at `stories:detail` (#45's minimal `story_detail` and its template are gone; the header and actions moved into `console/play/play.html`). Endpoints: `POST <id>/turn/`, `GET <id>/operations/<op>/poll/` (query `viewpoint`, `deadline`, `position`, `started`), `GET <id>/inspector/?operation=&origin=`, `GET <id>/transcript/?viewpoint=` (or the select's `output_viewpoint`, `default` resolved through the snapshot). `/stories/<id>/history/` is a placeholder for #52 so the transcript's "Older →" link resolves; `placeholder()` views now accept URL arguments.
  **Why:** The planner's endpoints and URL layout; one view module per area.
  **Issue:** #48
- **Decision:** The cast, story default viewpoint, status and `effective_budget.turn_deadline` travel in a signed hidden `snapshot` (`PlayStory`, salt `console.play.snapshot`); a missing, tampered or other-story snapshot is re-read. The page load reads the story, its settings (a failed settings read degrades to 45 s) and the history; a turn POST reads nothing before `respond`.
  **Why:** #45/#46's signed-hidden pattern; the test plan's "form error and zero Engine calls"; the turn timeout needs the deadline without an extra read per turn.
  **Issue:** #48
- **Decision:** HTMX wiring (htmx 4, checked with context7): the form `hx-post`s into `#inspector` (`hx-disable="this"`, `hx-sync="this:drop"`, `hx-config="timeout:645s"` so the browser outwaits the 600 s + 15 s maximum turn), and the answer swaps `#turn-status`, `#turn-form` and, after a finished turn, `#transcript` with `hx-swap-oob="true"`. The waiting card polls with `hx-trigger="load delay:1s"` and `hx-swap="outerHTML"`: each answer is the card again (re-armed) until the end, when a closing line replaces it and the inspector, form and transcript come out-of-band. Every view answers a full page to a request without `HX-Request`.
  **Why:** htmx 4 stops polling when the server returns the element without its trigger (load polling); `allowEmptySwapAfterOOB` is false, so the closing answer carries a main element.
  **Issue:** #48
- **Decision:** A 202's posted form is kept in the session (`console.play.pending`, keyed by the Engine operation id) so the poll that ends it can restore say/do (failure, cutoff) or clear them (committed); a card restored on page load without that entry re-renders a fresh form. While any card is live the form renders disabled.
  **Why:** The waiting card's GET cannot carry 16,000 characters of input; the Console DB already holds sessions.
  **Issue:** #48
- **Decision:** Inspector wording: busy "Story busy — another turn is running" / "Story busy — queue full (N waiting)" (`queue_depth`) / "Story busy" for `lock_timeout`, with the operation id and "Retry after N s"; a guardrail rejection "Input rejected by a guardrail — no reply was added." with the shared violations table and any `details.model_calls` report; `budget_below_safety_minimum` shows minimum required and max calls asked; `model_unavailable` purpose, reason, provider status, attempts and Retry-After; `erased_subject_referenced` the AC's sentence plus erasure id and match count. Those codes show an `HTTP <status> <code>: <message>` line instead of the full panel; every other error shows the shared panel. `validation_error`, `viewpoint_unresolved` and `budget_below_safety_minimum` are also placed beside the field (`ERROR_PATHS`). A refused form shows "The turn was not sent — correct the form." in the inspector.
  **Why:** The AC's wording per code; #43's 422 placement.
  **Issue:** #48
- **Decision:** `ViolationRow`, `violation_rows` and `stored_turn_report` moved from `console/views/operations.py` to the shared `console/turn_reports.py`; a new `console/cast.py` holds cast labels (duplicate names get "(…<last 6>)"), `default_actor`, `viewpoint_for`/`character_of`/`viewpoint_label`.
  **Why:** #51–#54 need the same character selects and report rows; #43's label rule had no helper yet.
  **Issue:** #48
- **Decision:** The fake Engine gained `respond_sequence(method, path, answers)` (ordered answers, the last repeats, each validated like `respond`).
  **Why:** The planner's scripted sequence per endpoint for polls.
  **Issue:** #48
- **Decision:** CI's `engine-up.sh` passes `EPISTREL_MODEL_ADAPTER=stub` (dropped for images without the setting) and `compose.smoke.yml` sets it too; the live smoke `test_play_one_turn_live` (`engine_live` + `engine_auth`) creates its own two-character story via the client, plays one turn through the play view and deletes the story in `finally`. Checked locally against `edge`, and in a browser (Playwright) against `edge` with `EPISTREL_STUB_LATENCY_S=6` and `turn_concurrency: queue`: viewpoint switch, a double-click making one turn (one log row), a queued turn's waiting card through to its result, and earlier-turn selection.
  **Why:** The test plan's smoke "against the Engine edge stub adapter"; without the adapter the edge Engine has no model and every turn is `model_unavailable`.
  **Issue:** #48
- **Decision (drift):** #49's "(planner, pass 2)" bullets "List page:", "Effective guardrails:", "Policy picker and the second write:" and "Errors and redirects:" are empty in the issue body and in its edit history (their sub-bullets were lost at planning). Built from the AC, the other planner lines and #47's patterns; each choice below.
  **Why:** The brief says not to ask; nothing recoverable remained.
  **Issue:** #49
- **Decision (drift):** No schema re-record for #49. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, stub adapter, torn down after) reports a match. Checked live: `inheritance_cycle` is 422 with `details.field = body.inherits_from`; retiring the default is 409 `validation_error` with `details.reason = "is_default"`; a PUT to a retired rating is 409 `details.reason = "retired"`; an identical PUT writes no version; creating a rating-scoped policy does not add it to the rating's `policy_ids`; the versions list comes newest first; effective guardrails list each policy once, from the nearest source; edge's default rating is Everyone (the demo's "Teen is default" holds only in the fixtures).
  **Why:** The conventions re-record only when the live document differs; the behaviours fix the error mapping.
  **Issue:** #49
- **Decision:** Client `get_content_rating`, `list_content_rating_versions`, `create_content_rating`, `update_content_rating`, `retire_content_rating` (204 → `None`); models `ContentRating` (`RatingDetailBody`), `EffectiveGuardrail`, `ContentRatingMutation` (`RatingMutation`), `RatingVersion` (a `RatingBody`) and `RatingVersionList`, all contract-checked; `ContentRatingSummary.retired`.
  **Why:** The planner's method and model names; #43's one-method-per-endpoint rule.
  **Issue:** #49
- **Decision:** The list always reads `include_retired=1` and "Show retired" (`?retired=1`) filters in the Console, so a retired parent still shows by name in "Inherits from"; Console-side paging at 50. Retired ratings have no Edit or Retire; the default keeps Retire.
  **Why:** One read serves both the rows and the parent names; #43's paging rule.
  **Issue:** #49
- **Decision:** Detail: own guardrails first, then inherited, in one table with an own/inherited column and the source rating linked; the inheritance is one line ("Everyone → Teen → Mature → Explicit") while every rating has at most one parent, else a nested `<ul>` (a loop or a depth over 64 stops the drawing). Versions sorted newest first: 20 on the detail page, and `/guardrails/ratings/<id>/versions/` (an added URL, name `ratings:versions`) pages all of them at 50.
  **Why:** AC "own distinguished from inherited", "nested when several parents"; the planner's "newest 20 with all versions paging".
  **Issue:** #49
- **Decision:** Policy picker (edit only) = live policies scoped to this rating, plus any id the rating applies now that is not one of them (retired, another rating's, unknown) so saving never drops it unseen — the Engine accepts those already on the rating. A failed policies or ratings read leaves that field out with a note, and the PUT resends the current value. Options (and the rating's name/version for the heading) travel in a signed `options` field; a tampered one is re-read.
  **Why:** Owner's "only this rating's policies"; #45/#46's signed-options pattern so refused input makes zero calls.
  **Issue:** #49
- **Decision:** Edit POST: validate, then re-read the rating and compare (`console/forms/ratings.py:unchanged`); no change → flash "No changes — no new version", no PUT. `inheritance_cycle` → "That would make <form name> inherit from itself" on `inherits_from`; any 409 on PUT → "Retired ratings cannot be changed"; other 422s placed by `apply_engine_errors`; anything else the panel above the kept form. Retire: confirm page; 409 `reason = is_default` → its own page with the message and a link to `/engine/`; other errors the shared panel.
  **Why:** The planner's error mapping.
  **Issue:** #49
- **Decision:** #47's `policy_new` adds a created rating-scoped policy to its rating (`console/rating_policies.py:attach_policy`: GET the rating, skip when already listed, else PUT with the id appended), with the idempotency key `<create key>.rating` so a replayed create replays it. On failure the page says "<policy> was not added to <rating>'s policies" above the error panel, with links to the policy and the rating's Edit page; on success a second flash names the rating's new version.
  **Why:** AC "second write, recorded separately; on failure the policy stays and the panel says it was not added".
  **Issue:** #49
- **Decision:** The ratings placeholder route is gone (`placeholder()` stays for `/engine/` and #52's history); `test_shell` no longer lists `/guardrails/ratings/` as a placeholder and the #47 navigation test checks only the policies side (the ratings side is in `tests/ratings`). Added an `engine_live` + `engine_auth` smoke running the demo against the real Engine.
  **Why:** The owning story replaces its placeholder (#43).
  **Issue:** #49
- **Decision (drift):** No schema re-record for #50. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, torn down after) reports a match; `POST /admin/stories/{id}/rebuild` (200 `RebuildResponse`, 202 `RebuildQueuedResponse`), `PUT /admin/settings` and `GET /admin/config-changes` are all in the recording.
  **Why:** The conventions re-record only when the live document differs.
  **Issue:** #50
- **Decision (Rule 3):** `ConfigChange` (`ConfigChangeItem`) reads the JSON key `class` into the field `change_class` through new field metadata (`console.engine.models.JSON_NAME`, `json_name(field)`); `_from_json` and the contract test's `compare_model` key fields by their JSON name.
  **Why:** `class` is a Python keyword, so the one-field-per-property dataclass convention could not name it.
  **Issue:** #50
- **Decision (Rule 2):** The operation log records the story of a story-maintenance admin call (`/admin/stories/{id}/…`, i.e. rebuild); other admin calls still have none.
  **Why:** A rebuild row without its story could not be found by the story filter, and the result page names the story from the row.
  **Issue:** #50
- **Decision:** Client methods `rebuild_story(story_id, *, idempotency_key) -> RebuildResult | RebuildQueued` (30 s, new `LONG_MUTATION_TIMEOUT_S`; 202 by status code), `update_admin_settings(patch, *, idempotency_key) -> DeploymentSettings` and `list_admin_config_changes(*, setting, change_class, since, after, limit=50) -> ConfigChanges` (query built by `_config_change_params`, which #54's story variant can reuse). The read reuses #45's `get_deployment_settings` rather than adding a `get_admin_settings`.
  **Why:** One method per endpoint (#43); the read already existed.
  **Issue:** #50
- **Decision (drift):** `DeploymentSettingsForm` lives in `console/forms/deployment.py` (forms became a package in #47), hand-written with small field factories that also record each schema default (`DEFAULTS`) and unit (`UNITS`). Nullable settings get `clear_<name>` checkboxes; `feedback_retention` is one `MultiValueField` (radio + days), so the guard test compares the non-`clear_` fields with `SettingsPatch` minus `profile_defaults` exactly; a second guard checks the defaults against `SettingsBody`.
  **Why:** The planner's hand-written form and guard test; keeping one form field per setting keeps change detection per top-level field.
  **Issue:** #50
- **Decision:** Settings the AC's section list does not name are placed: `worker_heartbeat_timeout`, `timer_lag_alert`, `lite_summary_chunk`, `structured_retry_budget`, `idempotency_result_window` under Turns; `thunks_max_open` under Caps; `public_base_url` under Media; `cascade_threshold`, `uncertain_below`, `rating_check_model_calls_per_record` under Verification and gates; `validation_model_calls`, `require_primary_image` under Catalog. Unit suffixes come from the name (`_days`, `_s`, `_ms`, `_bytes`) or the Engine meaning where the name has none (`idempotency_result_window` hours, media TTLs/intervals seconds, media sizes bytes).
  **Why:** The AC lists sections with "…"; every setting must be in exactly one.
  **Issue:** #50
- **Decision:** A blank nullable setting whose current value is set is a form error ("Enter a value, or tick Clear to unset it."); only Clear sends `null`. Clear on an already-unset setting is no change. A forged `clear_` on any other setting is ignored, and blank non-nullable inputs are "required" errors, so no other setting can send `null`. No change at all redirects with "No changes — nothing was sent." and makes no Engine call.
  **Why:** AC: Clear is the only way to send null; no change → no write.
  **Issue:** #50
- **Decision:** Settings errors: a 422 `superseded` becomes the non-field error "Moved to profile defaults: <replacement>"; other 422s go through `apply_engine_errors` with aliases `rating_retired` → `default_content_rating_id`, `profile_deleted` → `default_engine_profile_id` (the live Engine sends `details.field` for both); 409 `listing_status_in_use` names `details.value` (and `count`) at `listing_status_values`; 409 `feedback_tag_in_use` lists `details.in_use` tags with their current/history counts at `feedback_tags`. Anything else is the error panel above the kept form.
  **Why:** AC placements; error shapes checked against the live edge.
  **Issue:** #50
- **Decision (drift):** URLs end with `/` (#43): `/engine/settings/`, `/engine/config-changes/`; the rebuild result is `/engine/rebuild/<int:pk>/`, routed before `/engine/rebuild/<str:story_id>/` (ULIDs are never all digits). The story select GETs `/engine/rebuild/?story=<id>` with a signed id → name map, so the confirm page shows the name with no Engine call. The result page is found by the rebuild's idempotency key in the operation log (rendered inline if the row could not be written); "Back to story" only when `next` is a story page.
  **Why:** #43's URL and confirm conventions and pass 2's redirect-to-result.
  **Issue:** #50
- **Decision:** Held stories are labelled "(held) …<last 6 id characters>" and disabled; other non-active stories show their status in brackets ("(archived)") and stay enabled. Health highlighting uses #43's probe thresholds (`HEARTBEAT_LATE_S`, `OVERDUE_ALERT_S`) and marks values only while the Engine's state is not healthy (a database value other than ok/skipped is always marked).
  **Why:** The Engine's state is authoritative (#43); "…<id>" read as a truncated id, as #48's duplicate-name labels.
  **Issue:** #50
- **Decision:** Config changes: a shared partial `templates/console/_config_changes.html` fed by `console/config_changes.py:revision_groups(changes)` (consecutive rows of one revision under a header row naming time and actors; values as compact JSON cut at 120 with the full value in `<details>`); actor `console:<username>` links to `/operations/?operator=<username>` only when that user exists (pass 2). Filters in `console/forms/config_changes.py:ConfigChangeFilterForm(data, settings=setting_filter(RECORDED, path))` — enum select plus prefixed key from the recorded schema; both at once, a bad prefixed key, an unknown setting or class, or an unparsable `since` is a form error with zero calls. The class GET parameter is `change_class` (sent as `class`).
  **Why:** #54 reuses the partial, the grouping and the filter with the story endpoint.
  **Issue:** #50
- **Decision:** Profile-defaults values show as compact JSON (`null`, `true`, `"medium"`, `{}`); an empty object or a list is a leaf. "Edit profile defaults" is plain text naming `/engine/profile-defaults/edit/` (not built yet, M3 #84).
  **Why:** JSON keeps `null` distinct from the string "null"; the link would 404 until #84.
  **Issue:** #50
- **Decision (drift):** #49's rating-retire message now links `/engine/settings/` (was the `/engine/` placeholder), and the shell's placeholder test uses the story-history placeholder, since `/engine/` is built. A queued rebuild's #44 operation detail page shows the same honest job message (shared `console/engine/_rebuild_job.html`, `operation_log.rebuild_job_id` / `rebuild_job_text`).
  **Why:** The deployment settings now have their own page; the planner asks #44's detail to say the same.
  **Issue:** #50
- **Decision:** Several "(planner, pass 2)" headings in #50 (Story select:, Rebuild outcomes:, Settings form:, Health page:) have no sub-bullets; those parts were built from the AC and the round-one planner lines. Two live tests were added (`tests/engine_live/test_engine_panel_live.py`, `engine_auth`): rebuild through the panel, and change a setting then see it in Config changes by `console:nate` (restored afterwards).
  **Why:** As #49 noted, some pass-2 sub-bullets were lost in planning.
  **Issue:** #50
- **Decision (drift):** No schema re-record for #51. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, stub adapter, torn down after) reports a match; `POST /stories/{id}/ingest`, `IngestRequest`/`IngestEvent`/`IngestResponse`/`IngestedEventBody` are in the recording. The fixtures `ingest.json` and `get_history_ingested.json` were captured live and re-keyed to the test ids.
  **Why:** #43's schema convention.
  **Issue:** #51
- **Decision (drift):** The client event id allows 1-255 characters, not the planned 1-128. The pattern stays `^[A-Za-z0-9._:-]+$`.
  **Why:** The planner said to re-check against the schema. `IngestEvent.client_event_id` is `maxLength 255` with that pattern.
  **Issue:** #51
- **Decision (drift):** `client_event_conflict` reads `details.client_event_ids[]` (`{client_event_id, index}` each), and falls back to a flat `client_event_id` / `index`. It does not read only a flat `details.client_event_id`.
  **Why:** That is the shape both live `edge` and Engine `ClientEventConflict` send. A batch can conflict on several rows.
  **Issue:** #51
- **Decision:** Ingest refusals are placed on the rows as sent: `events[i].<field>` goes to that field (the form fields are named after `IngestEvent`, so `actor_character_id` rather than `actor`), `details.unknown_actor` (`{"events[i]": id}`) goes to that row's actor, `unsupported_event_kind`'s `details.locator` goes to the kind, and erased-subject and located guardrail violations go to the row's own errors. Anything naming no row goes to a batch error list above the rows. Refusals placed nowhere use #48's `error_inspector` wording (busy, budget minimum, model unavailable), shown inside the tab.
  **Why:** The AC needs row placement, and #48's inspector already words those codes. Live `edge` sends `unknown_actor` with `field: "body"`, which `apply_engine_errors` alone would only place as a batch error.
  **Issue:** #51
- **Decision:** The ingest POST is `stories:ingest` in `console/views/play.py`. Its non-view parts are in new modules: `console/ingest.py` (IngestView, ResultRow, error placement) and `console/forms/ingest.py` (the event form and formset). The tab switch is `GET /stories/<id>/?tab=` (with HTMX it returns only `_form_area.html`, built from the snapshot without an Engine read).
  **Why:** play.py would otherwise import a module that imports play.py. Keeping the tab partial on the play URL matches the planner's routes.
  **Issue:** #51
- **Decision (drift):** The tab swap replaces the turn form, so its viewpoint select is not in the DOM while the ingest tab is open. The ingest tab therefore carries the turn form's viewpoint in a hidden `output_viewpoint` field, which the tab link's `hx-include` copies across in both directions. This replaces an `hx-include` of the select from the ingest form.
  **Why:** It keeps the planner's intent (the transcript is re-fetched at the turn form's viewpoint) while only one form is in the DOM.
  **Issue:** #51
- **Decision:** #45's no-JS add/remove row editing moved to `console/formset_rows.py` (`edit_rows`, `wants_edit`, `rows`, `total_forms`). The new-story page now uses it too. The buttons keep #45's `name="add"` / `name="remove" value=<i>` instead of the planned `remove-<i>`.
  **Why:** #45's notes said to lift it once it was reused, and one convention across the Console is simpler.
  **Issue:** #51
- **Decision (Rule 1):** The play inspector no longer reads an ingest's operation-log row as a turn report. `_latest_inspector` excludes `kind=ingest`, and `selected_inspector` skips those rows, so clicking an ingested turn shows "No report — this turn was ingested, not generated".
  **Why:** An ingest answer carries the input gate's `model_calls` and `elapsed_ms`, which `turn_report_json` took for a turn report. That broke the planner's ingested-turn rule. A test covers it.
  **Issue:** #51
- **Decision:** A queued ingest (202, only when the queue wait outlasts the deadline) shows #48's waiting card. On `committed`, `poll` parses the stored body as `IngestResponse` and shows "Ingest recorded" with the transcript refreshed, not the turn wording.
  **Why:** The planner reuses the card and poll. Without this branch a committed ingest would read "Turn committed — result not available".
  **Issue:** #51
- **Decision:** I filed #129 (needs-triage): the recorded schema declares no 200 response for ingest, though the Engine answers 200 for replays. The fake-Engine tests model a replay as a 201 with the replay flags and the `Idempotent-Replay` header.
  **Why:** The schema gap is out of scope here. The fake only serves declared statuses.
  **Issue:** #51
- **Decision (drift):** No schema re-record for #52. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, stub adapter, torn down after) reports a match; `GET /stories/{id}/history` (all eight parameters, `audit`, `summary`) and `GET /stories/{id}/variants` (`VariantsBody`, `requires_check`) are in the recording. `get_history_audit.json` was captured live (a wordlist output policy rejecting the stub's drafts) and re-keyed to the test cast.
  **Why:** #43's schema convention.
  **Issue:** #52
- **Decision (drift):** The history response model keeps #48's name `History` (the issue calls it `HistoryResponse`); `summary` is now typed `list[SummarySegment] | None` and `audit_entries` parses `audit`. The audit record is not an Engine model: `console/history.py:AuditRecord` reads the item's `text`, which the Engine fills with the event payload's canonical JSON (`text`, `reason`, `attempt`, `policy_ids`, `violations[{policy_id, name, message, kind}]`); an erasure marker's non-JSON text shows raw. `get_story_settings` already existed (#48), so #52 adds `EffectivePolicy` and parsed `StorySettings.effective` / `.inactive` instead of a second method.
  **Why:** Renaming would churn #48/#51's callers; the Engine has no audit-record component.
  **Issue:** #52
- **Decision (drift):** The policy message shown in an audit record is the payload violation's `message`, not "the resolved policy's message": `EffectivePolicyBody` carries no message. The name still comes from the settings lookup (unresolved → raw id; `null` → "(name withheld — erasure pending)"), never from the payload's own `name`.
  **Why:** The settings GET has no message to resolve; the payload holds the message the guardrail applied.
  **Issue:** #52
- **Decision:** History controls (`console/forms/history.py:HistoryForm`): viewpoint and actor are choice fields over the cast, so a viewpoint/actor naming no cast member is a form error with no history call; `variant` passes through unchecked (the Engine's 409 `unknown_chain` / 422 shows in the panel). Submitting the controls drops `before_turn` (a new view starts at the newest page); "Older" keeps every current parameter and sets `before_turn`, and a "Newest page" link drops it. "clear" on the chain line drops only `variant`.
  **Why:** Paging cursor belongs to the view it came from; the planner names only the chain-view link's kept parameters.
  **Issue:** #52
- **Decision:** The viewpoint select's HTMX re-render (`hx-get` the same URL, `hx-include="closest form"`, htmx 4 leaves form values out of GETs otherwise) reads the story (cast, head) and, only with a `variant`, the variants (tip); it shows no form errors and makes no history call. A disabled audit box shows "enabled only with the omniscient viewpoint"; the refusal error text stays "Audit records need the omniscient viewpoint".
  **Why:** The fragment needs the cast for the selects; errors there would flash while the operator is still choosing.
  **Issue:** #52
- **Decision:** A turn group is keyed by `(turn, variant)` from both `turns` and the items; an audit record whose `(turn, variant)` has no group falls back to its turn's group, else is dropped. "No reply" = a turn ≥ 1 with no `message_rendered` / `message` / `narration` item, suppressed under an actor filter. The `#44` link lookup takes the newest row by `engine_operation_id`, then scans the story's newest 500 ingest rows' `response.results`.
  **Why:** The planner's grouping, "no reply" and link rules; the scan bound keeps one page from reading the whole log.
  **Issue:** #52
- **Decision:** With story history built, no placeholder routes remain: `console/views/placeholders.py` and `templates/console/placeholder.html` are deleted, and `tests/test_shell.py`'s placeholder test now checks that the history URL resolves to the real view and that no top-level route is a placeholder. Added an `engine_live` + `engine_auth` test (`tests/engine_live/test_history_live.py`) that plays a stub turn and browses it (character, omniscient with audit + summary, as of 0, 422 beyond head).
  **Why:** #43: the owning story replaces its placeholder; dead code would linger otherwise.
  **Issue:** #52
- **Decision (drift):** No schema re-record for #53. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, stub adapter, torn down after) reports a match; `regenerate`, `truncate`, `untruncate`, `swap`, `fork` and their bodies are in the recording. The fixtures `regenerate`, `untruncate`, `untruncate_swapped`, `swap`, `swap_active` and `fork` were captured live and re-keyed to the test ids.
  **Why:** #43's schema convention.
  **Issue:** #53
- **Decision (drift):** Regenerate is sent without `Prefer: respond-async` and has no 202 / waiting-card path. A running turn shows #48's busy message ("Story busy — another turn is running"). `test_regenerate_async.py` checks that the schema declares no 202 or `Prefer`, and that a 423 shows the busy message with no waiting card.
  **Why:** On `edge`, regenerate declares neither a 202 nor a `Prefer` parameter. It is "never queued" (423 `story_busy`), and the fake Engine only serves declared statuses. The planner's 202 → `?waiting=` path and the "Regenerating…" card cannot happen.
  **Issue:** #53
- **Decision (drift):** The Engine has no `swap_required` code. Undo after newer turns answers 200 `swapped: true` with `active_chain`, and the flash says "Rewind undone as a swap — new turns had been added since the rewind…". A 409 `nothing_to_undo` whose `details.options` include `swap` shows "Nothing to undo — there is no rewind to reverse. New turns were added since the rewind — swap to the old chain instead." with links to the Variants panel (`history/#variants`) and Fork. Without options it shows the first sentence only.
  **Why:** `edge` and Engine `core/timeline.py` turn that case into a swap. `nothing_to_undo` is what lists `options: ["swap","fork"]` and `dormant_chains`. The AC's wording and links are kept where they apply.
  **Issue:** #53
- **Decision:** Untruncate, swap and fork register their 202 (`RatingCheckStartedBody`) as `ReactivationCheck`. The Console shows only a one-line flash: "Guardrail re-check started — the story is paused while check <job> runs…". It redirects to the source play view. Following the check is #81.
  **Why:** The contract test needs each declared JSON status mapped to a model. Without that, a 202 would read as `bad_response`.
  **Issue:** #53
- **Decision:** All five operations live in `console/views/timeline.py`. The links and wording they share are in `console/timeline.py` (`Basis`, `Link`, `TurnControls`, `turn_controls`, `ARCHIVED_NOTE`, `reload_message`) and the forms in `console/forms/timeline.py` (`TimelineForm`, `SwapForm`, `RegenerateForm`, `ForkForm`, `parse_chain`, `default_fork_name`). Every success redirects to the play view, never to `next`: the AC's PRG target is the play view, and `next` is only the Cancel link.
  **Why:** `play.py` and `history.py` both build the links, and the view module imports `play.py`'s inspector wording, so the link builder has to sit outside the views to avoid an import cycle.
  **Issue:** #53
- **Decision:** Refusals render `timeline/refused.html`. A 409 `revision_conflict` shows only "The story changed since you loaded this page (now revision N) — reload" and a Reload link (N from `details.current_revision`). Everything else uses #48's `error_inspector` (busy, erased subject, guardrail violation, model unavailable, else the error panel) and adds `nothing_to_regenerate` / `nothing_to_undo` text and `unknown_chain` candidate links (swap links on swap, "Fork along" links on fork). Regenerate's 422 `validation_error` / `viewpoint_unresolved` (a stale `turn` included) and fork's `validation_error` re-render their page with the errors placed and the inputs kept.
  **Why:** The planner's conflict and placement rules, with #48's wording reused instead of duplicated.
  **Issue:** #53
- **Decision:** The play transcript's per-turn controls (`<li class="turn-controls" id="turn-N">` after each turn's last item, turn 0 first) carry the story read's revision and head on a full page. On an HTMX refresh (after a turn, ingest, poll or viewpoint change) they carry the history read's `timeline_revision` and `as_of`. Swap links appear only on a full page, because only the story read has the chains. The regenerate link also carries the signed play snapshot, so its confirm page gets the cast without a read; without the snapshot, it reads the story once.
  **Why:** #48's transcript partial must not read the story (`test_play_page` checks that). The history read answers at the same revision and head. Swap targets need `variant_chains`.
  **Issue:** #53
- **Decision:** The swap call waits `turn_timeout(None)` (45 s + 15 s). Swap links do not carry the story's turn deadline.
  **Why:** #43 counts swap as a turn-producing call, and the confirm GET makes no read to learn the deadline.
  **Issue:** #53
- **Decision:** On the history page, each Variants row gets Swap (hidden on active chains, `&check=1` when `requires_check`) and "Fork" at the chain's `tip_turn` with `variant_chain=T:V`. The panel gets `id="variants"`. An archived story disables Swap. Added an `engine_live` + `engine_auth` test (`tests/engine_live/test_timeline_live.py`) that runs the issue's demo: regenerate, rewind, a stale second rewind refused, undo, swap back, and fork with its banner.
  **Why:** The planner's Variants-panel placement. A fork must lie on its chain, and the tip is on it.
  **Issue:** #53
