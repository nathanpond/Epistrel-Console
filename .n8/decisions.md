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
- **Decision (drift):** No schema re-record for #54. `engine_schema --check` against a fresh Engine `edge` (127.0.0.1:18000, stub adapter, torn down after) reports a match; the settings `PATCH`, pinned-memories and custom-instructions `GET`/`PUT` and `GET …/config-changes` are in the recording. The error shapes were checked live: `superseded` arrives as 422 `validation_error` with `details.reason: superseded` and `details.replacement`; `cannot_relax` names `body.policies[0].effect`; the pinned cap is 422 `validation_error` with `details.{field: body.items, cap, limit, actual}`.
  **Why:** #43's schema convention; the AC's wording is mapped onto what `edge` sends.
  **Issue:** #54
- **Decision:** `/stories/<id>/settings/` reads the story (its error escapes to the page panel), then settings, `/admin/settings`, pinned memories, custom instructions and config changes, each degrading only its section. Each form posts to its own URL with one signed snapshot (story, cast, status, settings as read, the deployment's three defaults and two caps). A refused or edited submit (invalid form, Engine refusal, pinned add/remove/up/down) re-renders only that section from the snapshot (`settings/section.html`), with the input kept and no Engine read; a save redirects to the page's anchor with a flash.
  **Why:** The test plan needs "refused by the form with zero calls", and re-reading six endpoints to re-show one form would break that.
  **Issue:** #54
- **Decision:** No call sends `X-Epistrel-Expected-Revision`, so the planner's "409 shows #53's reload message" for pinned/instructions has no path; a 409 `revision_conflict` / `timeline_changed` would render the error panel.
  **Why:** Pass 2 and #43 ("expected revision only where an AC names it") override the earlier planner line.
  **Issue:** #54
- **Decision:** Settings form: a blank nullable input (and the viewpoint's "no override" option) means `null`; "Reset to default" (shown only on "story" fields) sends `null`, except on a nullable field already `null`, which sends nothing. Changed = differs from the snapshot's value (bools by type). A constant-default field equal to its default reads "default" even if stored.
  **Why:** Merge-patch convention (#43, #50); the planner's marker rules.
  **Issue:** #54
- **Decision (drift):** Story policies reuse #47's `PolicyContentForm` per-type rule inputs (`STORY_RULE_TYPES`), not a JSON rule textarea; `StoryPolicyForm` adds the scope select (story / `character:<id>`), an effect select (deny/allow) and a hidden `policy_id`. Editing is `?policy=<id>`: it reads `GET /admin/policies/{id}` for the content (`EffectivePolicyBody` has no rule or message) and is refused without a call for an id not in the story's named story/character policies. `_content_fields.html` now renders hidden fields separately and shows the fixed "Effect: deny" line only for forms without an effect select (`shows_effect`). `cannot_relax` shows "A story policy can only add restrictions — <Engine message>" at the non-field errors; other 422s go through `STORY_POLICY_ERROR_PATHS` (`policies[0].<path>` → #47's placement).
  **Why:** #47 built per-type inputs and the helper inventory says reuse them; one form shape across both policy pages.
  **Issue:** #54
- **Decision:** The effective / inactive tables show a character policy's scope as `character` without naming the character.
  **Why:** `EffectivePolicyBody` carries no `scope_id`; naming it would mean an extra read per policy.
  **Issue:** #54
- **Decision:** `console/formset_rows.py` gained `up` / `down` buttons (value = row index, swapped with the neighbour; ids move with their rows). The pinned formset uses it with `min_rows=0` and `max_rows` = the cap, and Add is hidden at the cap. `ConfigChangeFilterForm` gained `groups=` (option groups) and `with_since=False`; the story's setting select groups the recorded enum as Settings / Historical / Profile overrides / Rating and profile.
  **Why:** Extend the shared helpers rather than copy them (#51's lift).
  **Issue:** #54
- **Decision:** Pinned / instructions refusals: a cap 422 and `pin_limit_exceeded` show their cap or scope, limit and actual as facts; a field 422 is placed on its row; a guardrail rejection lists the violations (#44's table) and marks the `items[n]` row its locator names; `erased_subject_referenced` shows the AC's message and marks the row when `details.field` / `locator` / `path` names `items[n]`; `story_busy` uses #48's `error_inspector` busy line. A write with `operation_id: null` flashes "No change … nothing was written".
  **Why:** The AC's wording, with #48's busy wording reused.
  **Issue:** #54
- **Decision:** The "Moved to engine profile overrides — see Config" note and the budget's "Change in Config" link point at `/stories/<id>/config/`, which 404s until M3 #85 builds it. An archived story's forms render inside a disabled fieldset with "Archived — unarchive to change the settings". Added `tests/engine_live/test_story_settings_live.py` (`engine_auth`), which runs the demo against `edge`.
  **Why:** The test plan names the link; #43's archived convention.
  **Issue:** #54
- **Decision (Rule 3):** CodeQL failed the milestone PR on `py/incomplete-url-substring-sanitization` at `tests/test_status_views.py:87`, an M0 test assertion (`"http://engine.test:8000" in html`) flagged because #43 changed the file. It is a false positive in a test, but rather than dismiss it the assertion now checks the view's `engine_url` context value and the exact `<dd>` the status page renders, which is also the stricter test.
  **Why:** The merge gate requires CodeQL; dismissing an alert is the owner's call, tightening a test is not.
  **Issue:** #43

## /n8-exec M2 (verification fixes) — 2026-10-24

- **Decision:** Re-ran `/n8-exec M2` as a fix pass for the bugs `/n8-verify M2` filed (#130, #131, #132). PR #128 had already merged M2's stories. Branch `milestone/m2-verification-fixes`, like M1's earlier verification-fix PRs.
  **Why:** M2's only open non-epic issues are those three bugs. M1 set the precedent for running verification fixes through `/n8-exec`.
  **Issue:** #130, #131, #132
- **Decision (Rule 1):** `turn_report_json()` reads a 422/503's `error.details` as a turn report only when its `model_calls` is a **list**. The new `input_gate_spend()` reads a 422 `guardrail_violation`'s integer `model_calls` and `elapsed_ms` (Engine `orchestrator/policies/input_gate.py:118` sends `extra={"model_calls": <int>, "elapsed_ms": <int>}`). The play inspector (live refusal and logged row) and the operation detail page now show "Input gate: N model call(s), M ms", the ingest tab's wording, shared as `turn_reports.spend_text`. The inspector shows it as a fact; the operation detail page shows it as a line in "Input rejected". The guardrail refusal no longer builds a `TurnReport`.
  **Why:** One root cause. The integer count was dropped (#130), and it also made `is_turn` true, which produced the false "could not be read" notice (#131).
  **Issue:** #130, #131
- **Decision:** The play view's first-load and clicked-item lookups (`_latest_inspector`, `selected_inspector`) now pick a row when it has a turn report **or** an input gate spend (`_inspectable`).
  **Why:** With live data, those lookups used to select rejected-input rows because their `details` carried `model_calls`. Keeping that explicit means the root-cause fix does not quietly change which row the play view shows.
  **Issue:** #130
- **Decision:** These tests now use the live integer shape instead of hand-built `model_calls` lists:
  - `respond_guardrail_violation.json` gained `model_calls: 1, elapsed_ms: 3`.
  - `test_turn_outcomes`, `test_inspector` and `test_ingest`'s `guardrail()` helper now send an integer count.
  - `test_a_422_with_model_calls_in_details_is_a_turn_row` became `test_a_503_…`: a `model_unavailable` 503, the case where details carry a list (Engine `lite/pipeline.py:862`).

  `test_ingest` was not named in #130. It had the same false green on the same shared refusal path, so it was fixed here.
  **Why:** #130's evidence: these tests stayed green on a shape the Engine never sends.
  **Issue:** #130
- **Decision:** The story-settings pinned/instructions guardrail refusal (`views/story_settings.py`) still lists only the violations, without the gate spend.
  **Why:** That AC (#54) does not ask for the spend, and no bug was filed against it. It is out of scope for this pass.
  **Issue:** #130

## /n8-plan M4 — 2026-10-24

- **Decision:** M4 is sliced into 22 stories (#57–#59 expanded, #134–#152 new) over epics #15–#18. The surface is the epics' 19 AC items: 18 in scope, all covered (fresh checker), with 10 Engine M4 capabilities pulled in by request (guarantees, scene/profile, embedding and re-embed, author facts, quarantine/sanitize, pins, consolidate, predicate proposals, escalation-yield and shadow reports).
  **Why:** Owner, round one: "ok" to every recommendation; the Console exists to drive every Engine capability.
- **Decision:** Listing eval runs (#18's first AC) is descoped from M4. The Engine serves eval runs only as committed reports, not over REST. Requested upstream as nathanpond/Epistrel#532.
  **Why:** The Console talks to the Engine only through its public REST API (CLAUDE.md).
- **Decision:** "Browse memory tiers" and "consolidation outputs" are narrowed to the memories one retrieval question returns, labelled "not a full list", until the Engine lists memories (nathanpond/Epistrel#531).
  **Why:** The Engine has no memory-list route; owner, round two: "recs good".
- **Decision:** Re-running a past turn's retrieval asks as the acting character, with the turn's input, as of the turn before, behind a banner naming what differs from the turn's own per-render retrieval. Consolidation is offered for the shared scope only.
  **Why:** The retrieve route accepts only the omniscient viewpoint. In Engine M4, all render memory is shared, so a characters scope writes nothing. Owner, round two: "recs good".
- **Decision:** M4-wide conventions live in #135 (14 conventions). The opt-in confirm mode (`confirm_action(require_confirmed=True)`) is its own story, #134, and blocks every destructive M4 write.
  **Why:** 735 + 726 executor-simulation points recurred across stories. Engine-wide calls are routed once.
- **Decision:** A sanitize keeps no span, surrogate or memory text anywhere in the Console's operation log (request, stored response, detail page). Past-turn explain links are built from history items, never from the logged request.
  **Why:** Sanitize and erasure exist to remove text. The Console's own log and URLs must not keep it.
- **Decision:** Engine docs links (capability map, guarantee table) point at the Engine repo's `main`, not the version tag. #46's model-guide link has the same defect, filed as #153 (M9).
  **Why:** Release tags (v0.1.1) predate these docs.
- **Decision:** Filed upstream on the Engine: #531 (memory list), #532 (eval runs), #533 (ingest 200 undeclared, Console #129), #534 (eval hooks refused inside the result window), #535 (single-fact read), #536 (`viewpoint_unresolved` undeclared on scene and entity profile). Triage: #129 and #91 moved to M9; #61–#63 wait for M5.
  **Why:** API gaps found while planning; owner approved in rounds one and two.
- **Decision:** M4 is blocked on unbuilt M3 stories (#64, #78, #84, #85, #87) through native blocked-by edges.
  **Why:** The Inspect area reads #85's effective config and follows #64's conventions. M3 must execute first.

## /n8-exec M3 — 2026-10-24

- **Decision (drift):** The client sends `X-Epistrel-Principal` as Latin-1 bytes when the principal fits Latin-1 (`café` arrives as `café`). A principal outside Latin-1 still goes as percent-encoded UTF-8, with its length counted on the encoded value. Only a default `console:<username>` can be outside Latin-1, because the acting selector refuses such values. The client also refuses C1 controls (U+0080–U+009F) now.
  **Why:** #64 pass 2 says the Engine decodes headers as Latin-1 (Starlette does), which supersedes the M1 decision to percent-encode every non-ASCII principal for Latin-1 values. A raw C1 byte would reach the Engine's `has_control`, so the client refuses it before sending. M1's client tests were updated to match.
  **Issue:** #64
- **Decision:** A recorded call with no `@operation(entity=…)` whose row has a `story_id` is recorded as `entity_type="story"`, `entity_id=story_id`. The migration back-fills old rows the same way. The existing story methods are not re-registered with `entity="story"`.
  **Why:** This is convention 9's "story calls set entity_type=story alongside story_id", applied in one place, and it covers every current and future story call.
  **Issue:** #64
- **Decision:** `Operation.recorded` defaults to `None`, meaning "by method" (`is_recorded`). `@operation` captures the bound arguments in `Call.arguments` only when the registration names an `entity_id`. An `entity_id` that names no method parameter fails at import.
  **Why:** Code that builds `Operation(...)` directly, like #44's tests, keeps recording mutations. Methods without an entity pay nothing per call.
  **Issue:** #64
- **Decision:** The nav's M3 links resolve in the `shell` context processor (`reverse`, skipped on `NoReverseMatch`), not with `{% url … as href %}` in the template. Operations moved after the M3 sections and Admin was added last, per the nav order in the issue.
  **Why:** The behaviour is the same (each link appears once its URL exists), the logic is testable in Python, and the Catalog group label's "at least one child resolves" rule stays in one place.
  **Issue:** #64
- **Decision:** Shared helpers added beyond the named blocks: `console/engine/pool.py` `fan_out()` (convention 12's 8-worker / 10 s pool, since no #41 pool existed to reuse), `console/reports.py` (anchors, `report_from_json`, `report_from_row` for `?report=<pk>`), and `console/templatetags/entities.py` (`entity_link`, `entity_display`).
  **Why:** `story_labels` needs the pool, and the report partials need the anchor rule. Later stories reuse all three.
  **Issue:** #64
- **Decision:** `rating_choices` reads `list_content_ratings(include_retired=True)` once and filters retired ratings itself. It returns `RatingChoices(choices, error)` rather than raising.
  **Why:** One read can name a retired `current` ("<name> (retired)"). The failed-read text ("Ratings unavailable: …") belongs to the form.
  **Issue:** #64
- **Decision (drift):** The Engine's `CharacterCard` has no `current_version`. The versions page takes the latest version from the latest card read's `version`. Image "slot" is the image's place in `images` ("primary", "gallery N", "reference N"), because the Engine has no `slot` field.
  **Why:** The plan names fields the recorded schema does not have. The Engine's schema wins.
  **Issue:** #65
- **Decision:** `get_catalog_character_card` is not registered with `@operation`. `get_catalog_character` (full) holds the `GET /catalog/characters/{character_id}` registry key. The contract helper now accepts an `anyOf`-of-components response when the model matches one option, and `CatalogCharacterCard` is checked against `CharacterCard` directly.
  **Why:** The registry is keyed by method and path, so two methods on one route would overwrite each other. Reads are never recorded, so nothing else depends on the registration.
  **Issue:** #65
- **Decision:** Disposition `setting` and reference-image `audience` are `str` fields with `OPEN_ENUM` metadata (`console.engine.models.open_enum()`). The contract test compares them as the schema's enum. A plain `str` against an enum is still reported.
  **Why:** The plan asks for `str`, so an unknown Engine value renders raw instead of failing the whole read. The contract test still bites on enum drift.
  **Issue:** #65
- **Decision:** The proxy tests serve the sample files through a new `FakeEngine.side_effect(method, path, handler)` (declared route; the answered status is checked against the schema), not respx.
  **Why:** respx is not a dependency, and the fake Engine already holds the schema checks. Adding a library for one test file is not in the plan.
  **Issue:** #65
- **Decision:** `_request` accepts a query as a mapping or as `(name, value)` pairs (`QueryParams`), so `label` and `relation` repeat. Header building moved into `_wire_headers` and `_http`, which the streaming media read shares. The relation filter uses two GET fields, `relation` (name) and `relation_principal`.
  **Why:** Repeated parameters need pairs. The media stream must send the same headers (key, principal, User-Agent) without going through `_request`'s buffered call.
  **Issue:** #65
- **Decision:** Django adds `Content-Length: 0` to the proxy's 304. Apart from that, the 304 carries only ETag, Cache-Control and the two security headers. The proxy answers HEAD with 405 (GET only, as planned).
  **Why:** `CommonMiddleware` sets Content-Length on every non-streaming response. A zero length on an empty 304 is harmless.
  **Issue:** #65
- **Decision (drift):** `get_catalog_character_companion` is registered without a response model. It parses a narrow `CompanionDraft` (id, status, versions, `companion_of`, `draft_revision`), and a contract test checks each of those fields against `ScenarioFull`.
  **Why:** The companion read answers a whole `ScenarioFull`. The contract check needs a model to match the component exactly, and the full scenario model is #68's to build.
  **Issue:** #66
- **Decision:** The contract tests compare registered operations and models against the recorded schema with `EntryLocation` widened by `ElementLocation.media_id`. This is the same widening #64's report test already used. A new test proves that `media_id` is the only difference.
  **Why:** `CharacterPublished` holds the shared `ValidationReport`, whose `EntryLocation` parses both of the Engine's location shapes (#64). Without the widening, the publish registration could not match.
  **Issue:** #66
- **Decision:** The edit form carries a signed snapshot. It holds the draft as rendered (the merge-patch original), its revision and header facts, the companion revision (or the read error) and the rating options. A POST validates from it with no Engine read. A missing or tampered snapshot redirects to the edit page, which reads again. The create page signs the rating options the same way.
  **Why:** The plan requires that an invalid form makes zero calls, including invalid `adapter_params` and a nuance with no setting. The rating select's options are needed to validate, so they travel signed rather than being read again.
  **Issue:** #66
- **Decision:** On edit, the visual identity diffs field by field, so each cleared field sends `null`. A draft with no stored visual identity whose fields stay empty sends nothing. An `adapter_params` of `{}` counts as blank. Only "Clear visual identity" sends `visual_identity: null`.
  **Why:** Sending `visual_identity: null` whenever every text field is cleared would also delete the reference images (#71's). Sending `{}` would make a call with no change.
  **Issue:** #66
- **Decision:** If Save & publish saves and the publish then gets a 409, the Console redirects to the edit page. The flash says "Saved as draft revision R, but not published." and the stale banner follows as a second flash. `?companion=1` keeps the box ticked. A 409 with no save first re-renders the page with the banner and the input kept. Errors on index paths in comma-separated labels read "Label N:" rather than "Line N:".
  **Why:** After a save, the snapshot is stale, so the page has to be read again. The redirect still keeps the checkbox state, as the plan requires. Labels are not one per line.
  **Issue:** #66
- **Decision:** New shared helpers: `console/draft_stale.py` `stale_banner(error, *, draft_revision, companion_draft_revision=None)` (#64 convention 2's banner, naming the stale side) and `console/reports.py` `publish_reports(pk, *, entity_id=None) -> list[LabelledReport]` (a publish's character and companion reports read back from the #44 row). `confirm.posted_idempotency_key` gained an optional field name, because a page with two writes carries two keys.
  **Why:** No earlier story built the draft-stale banner. The scenario editor (#68/#74) needs the same banner and report read-back.
  **Issue:** #66
- **Decision:** The detail page shows Publish only on full views of the latest version or the draft, never on the card view. On the latest view it reuses the draft read that "draft differs" already makes, then makes one companion read. The catalog test fixture answers the companion read by default.
  **Why:** The plan names the latest and draft views. The card view has no draft read. #65's detail tests would otherwise fail on the new read.
  **Issue:** #66
- **Decision:** The persona edit snapshot is signed with a one-hour max age (`twostage.MAX_AGE_S`). A tampered or expired snapshot re-reads the persona, keeps the posted input and shows "This form expired — your changes were not saved; check and submit again". It never PATCHes.
  **Why:** The plan names "tampered or expired". #43's snapshots have no max age, so the one-hour limit M3 already uses for signed confirmations gives "expired" a meaning.
  **Issue:** #67
- **Decision:** The delete confirm page posts the list to return to in a hidden `back` field. Cancel (the shared partial's `next`) goes to the detail page. The list links each detail page with `?back=<list URL>`, and the detail page passes it on to Delete as `next`. Every return URL is checked as same-origin, else `/personas/`.
  **Why:** The plan sends Cancel to the detail page but the delete to the list it came from. The shared confirm partial carries only one `next`.
  **Issue:** #67
- **Decision:** A 422 on persona create or edit is placed at its fields (`apply_engine_errors`). The error panel shows only when no field could be placed. Other errors, including a 503 `erasure_pending`, show #43's panel. Detail, edit-read and delete errors escape to the shared panel.
  **Why:** The plan names placement for `persona_name_taken` only. This matches #66's handling of field errors.
  **Issue:** #67
- **Decision:** The scenario editor's fields follow #64 convention 6's `__` names throughout, including the opening (`starting_state__opening__narration`), so an Engine path maps to its field directly. The plan's extra alias `starting_state.opening.<f>` → `opening_<f>` isn't needed. The rule "an `opening` key typed in a box is refused" was dropped: each deferred sub-key has its own box, so no box can hold the opening.
  **Why:** One naming rule keeps 422 placement and report anchors uniform. With one box per sub-key, the refusal has nothing to guard.
  **Issue:** #68
- **Decision:** `create_scenario` and `patch_scenario` take `idempotency_key` (as #66's character writes do), and `list_scenarios` takes `labels`/`relations` sequences, so `CatalogFilterForm.query()` feeds it unchanged. It returns `(items, next_cursor)` as planned. A narrow `list_engine_profiles` → `EngineProfileSummary` (registered without a response model; a contract test checks each field against `EngineProfile`) backs the new `engine_profile_choices`. That follows cursors over deleted profiles too, so a deleted current profile keeps its name, and degrades to a free-text id above 500 profiles.
  **Why:** The planned signatures had no idempotency key, but #43's per-render key applies to every M3 write. #78 owns the full profile model.
  **Issue:** #68
- **Decision:** `JsonBoxField` (#64) now also refuses a key given twice in one object ("Enter valid JSON: duplicate key 'a'"). The existing NaN/Infinity messages stay as they were, not the plan's single "Must be valid JSON (no NaN, Infinity or duplicate keys)" text.
  **Why:** Changing the shared message would break #64's pinned tests for no gain; each message already names the problem.
  **Issue:** #68
- **Decision:** On an archived scenario the edit page renders every input and Save disabled with "Archived — unarchive to edit". A forced POST is still sent, and the Engine's 422 `entry_archived` shows in the error panel. #66 instead redirects without a call.
  **Why:** The plan says "a forced POST shows `entry_archived`", and the test plan asks for the Engine refusal to be rendered. The inputs are disabled only in HTML, so a crafted POST still carries its values.
  **Issue:** #68
- **Decision:** No-JS row editing for pages with several formsets goes through one `op` button (`console/formset_rows.py` `parse_op`, `apply_op`: `add:<prefix>`, `remove:<prefix>-<n>`, `refresh`). A re-render is "quieted" (`ScenarioEditor.quiet()`: errors set empty, so Django does not validate) instead of carrying a `show_errors` flag into every template. A visually hidden default Save button comes first in the form, so Enter in a text input saves rather than pressing the first row button. Policy rows have their own blankness rule: a rule box that is blank or an untouched template does not count, so a new row left alone is dropped.
  **Why:** Convention 6 names the `op` buttons, and #73 adds the `role` formset to the same editor. Django's `has_changed` would otherwise treat a pre-filled template as input.
  **Issue:** #68
- **Decision:** When the ratings read fails, the content-rating field is left out and the saved rating is kept, as #66 does, instead of a disabled select. The note "Ratings unavailable: … — reload to retry" shows in its place.
  **Why:** One behaviour across the catalog editors. A disabled field would post nothing and read as "cleared".
  **Issue:** #68
- **Decision (drift):** Lifecycle routes are named inside the existing namespaces (`catalog_characters:archive|unarchive|delete|listing-status`, `scenarios:…`), not `catalog-<entry>-<action>`, and their path parameter is `entry_id`. The fixtures live in `tests/fixtures/engine/` (`catalog_characters_status.json`, `catalog_scenarios_status.json`, `catalog_listing_status.json`, `error_companion_follows_character.json`, `error_invalid_listing_status.json`), not `tests/fixtures/catalog/`.
  **Why:** Convention 20 puts each root under its namespace and the repo has one fixture directory, which `engine_fixture()` reads.
  **Issue:** #69
- **Decision:** The archive and unarchive flashes add "(its companion follows)" when the response's `companion_id` is set. The delete and listing-status flashes, which get no such field back, use the posted `has_companion`. The suffix goes before the final period: "Archived *Name* (its companion follows)."
  **Why:** The answer is the truth where there is one. The display data covers only the calls that return nothing about the companion.
  **Issue:** #69
- **Decision:** The edit pages make no read for the archived banner. A companion's banner names its character by id there; the detail page names it through the companion-of card read it already makes. The catalog test `engine` fixture now answers `/admin/settings` by default, since every full detail view reads it once for the listing-status select.
  **Why:** #66 and #68 keep a re-rendered or invalid edit form at zero Engine calls, and the banner renders on those paths too.
  **Issue:** #69
- **Decision:** A "Follows" helper type (`catalog_lifecycle.Follows`) is separate from #68's `CompanionLabel`, and the scenario detail converts one into the other. #66's Console-side refusal of a POST from an archived character's edit page (a flash, no call) stays. The forced-POST complement test sends a POST from a page rendered before the entry was archived, so the Engine's `entry_archived` panel shows for both entry types.
  **Why:** This avoids a circular import between the scenario and lifecycle views. An archived page's disabled character fields post their initial values, which make an empty patch, so only a stale page can reach the Engine.
  **Issue:** #69
- **Decision:** `MediaUploadRequest` is a frozen dataclass whose `to_json()` leaves out every field that is `None` except `depicted_subjects`. It is not a pydantic model as planned.
  **Why:** pydantic is not a dependency of the Console. The models are dataclasses, and adding a library to build one request body is out of scope (Rule 4). The behaviour is the planned `exclude_none=True`.
  **Issue:** #70
- **Decision:** `EngineClient._request` gains `files=` (multipart parts, streamed by httpx), and `@operation` gains a `record_body` hook. The recorder stores `hook(arguments)` as the row's request body. `upload_media` uses it to record `{metadata, file: {name, size, content_type}, poster_file}`.
  **Why:** The recorder sees only `json=` today. A hook on the registration keeps file bytes out of the log without special-casing paths in `operation_log`.
  **Issue:** #70
- **Decision (drift):** The 413 *limit* also reads `details.threshold`, after `limit`, `media_max_bytes` and `media_multipart_threshold`. The Engine's `use_multipart` names its threshold `threshold` (`core/media/assets.py _use_multipart`), and `media_too_large` uses `limit`.
  **Why:** Without it, the `use_multipart` message could never show its size. The Engine is authoritative.
  **Issue:** #70
- **Decision (drift):** The 415 "Poster:" prefix follows the plan: it shows when a string in `details` names `poster_file`. At this Engine version, a poster's 415 `details` (`reason`, `mime_type`, `declared`, `allowed`) does not name the part, so the real Engine shows the unprefixed message for a refused poster.
  **Why:** The Console cannot tell the parts apart until the Engine names one. The rule costs nothing and starts working once the Engine names the part.
  **Issue:** #70
- **Decision:** The subject picker's sources and the media lookups are shared helpers in `console/engine/choices.py`: `subject_choices(client)` and `media_lookups(client, ids)`. The 500 cap counts items read, not distinct ids, so a page that repeats itself still ends. The catalog source reads `status=published` and then `status=draft` at 200 per page, and both statuses count toward one cap.
  **Why:** #71 reuses the per-asset lookup. The picker is the shape #86's subject inputs need. Counting distinct ids looped forever on a repeating cursor.
  **Issue:** #70
- **Decision:** The tests answer the Engine through `FakeEngine` (`respond` and `side_effect`), not respx. The sample files are #65's `tests/fixtures/engine/media/sample.{png,mp4}`. New JSON fixtures are `get_media.json` and `upload_media.json`.
  **Why:** The repo has no respx. FakeEngine validates canned bodies against the schema, and #65's samples are the one shared location (under `fixtures/engine/`).
  **Issue:** #70
- **Decision:** When the rating read fails, the upload form refuses a POST with the "Ratings unavailable" text as a non-field error, and no `POST /media` is made. The Upload button is also disabled.
  **Why:** The rating is required and comes only from the select. A crafted POST would otherwise reach `metadata()` without one.
  **Issue:** #70
- **Decision:** On a re-render after a refused form or a media row button, the edit page shows each slot's metadata from the signed snapshot. The snapshot holds the per-slot lookups made when the page was read, so the page makes no new `GET /media/{id}`. An id typed since then shows its thumbnail through the proxy but no metadata until the slot-row partial looks it up. The plan said to re-run the lookups on an error re-render.
  **Why:** #64/#66 hold that an invalid form makes zero Engine calls, and the AC says an unset audience "makes zero calls". Re-running the lookups would break both.
  **Issue:** #71
- **Decision (drift):** Engine paths are read in bracket form (`body.public_card.images.gallery[1]`, `visual_identity.reference_images[1].audience`), and the plan's `.<i>.` spelling is accepted too. Refusals are placed by a `MediaSections.place` hook that `CharacterForm.place_error` and `ScenarioEditor.place_error` offer each failure first (new keyword `extra=`). The plan instead had `apply_engine_errors` gain a `formsets=` argument.
  **Why:** The Engine's `validation_error`, `media_retired.details.field` and report locations all use brackets. Neither editor uses `apply_engine_errors`; each has its own `place_error`.
  **Issue:** #71
- **Decision:** A POST that has no management form for a media list keeps that list's draft value (unchanged, so not sent). A POST without the primary or cover field keeps the draft's primary or cover. Ordering does not use Django's `ordered_forms`: rows sort by `ORDER`, and a blank `ORDER` takes the row's rendered position, ties keeping rendered order. The up/down buttons renumber `ORDER`.
  **Why:** A POST built without the sections (#66/#68's tests, an old tab) must not clear the images. Django puts blank `ORDER` values last, but the plan says blank keeps the rendered order.
  **Issue:** #71
- **Decision:** Duplicate refusal reads "Already in this list (row N)" for the character gallery, the scenario gallery and the videos. N is the rendered row of the first occurrence. The audience select's blank choice is a server-side error only (no HTML `required`), since the blank extra row would otherwise block every submit. The character form gained a hidden default Save button, so Enter never triggers a media row button.
  **Why:** Pass 2 of the plan overrides pass 1's "Already in this gallery". The other two are needed for the form to work without JS.
  **Issue:** #71
- **Decision:** A failed per-slot read shows "<id> unavailable (<status> <code>)" with no `<img>` (the Engine's 404 code is `not_found`). On detail pages, `<img alt>` is the read's alt text, else "". The scenario full read has only ids, so it uses the looked-up asset's alt text. #65's `characters/_image.html` was replaced by the shared `media/_shown.html`, and the generation-metadata `<details>` was kept.
  **Why:** The AC says never a broken image alone. `ScenarioMedia` has no alt text.
  **Issue:** #71
- **Decision (drift):** A full cast is recognised by `details.cap == "cast_max"` as well as `reason: cast_full`, and a duplicate name by `reason: duplicate`. The fixtures `tests/fixtures/engine/stories/error_validation_error_{cast_full,duplicate}.json` record the Engine's real details.
  **Why:** The Engine (`core.stories._cast_full` / `_name_taken`, b4adb1a) sends `{field: "body", cap: "cast_max", limit, actual}` and `{field: "body.name", reason: "duplicate"}`. There is no `reason: cast_full`, which the plan assumed.
  **Issue:** #72
- **Decision (drift):** The readiness banner leaves out "likes & dislikes". It adds the visual identity's preferred image model, base appearance prompt, negative prompt and seed when the draft has a visual identity.
  **Why:** The Engine's publish check (`PUBLISH_REQUIRED`, `IDENTITY_REQUIRED` in `core.catalog.models`) does not require `likes_dislikes` (lists may be empty), but it does require those four identity fields. The banner promises "Before this can be published", so it follows what the publish actually refuses.
  **Issue:** #72
- **Decision:** The routes are named `stories:character-new` and `stories:character-promote`, not the planner's `story_character_new` / `story_character_promote`. The readiness banner is computed on the edit GET and carried in the edit snapshot, so a refused save re-renders it without new reads.
  **Why:** The names follow the `stories` namespace's existing hyphenated style (`settings-pinned`, `undo-rewind`). Carrying the banner keeps it based on the saved draft, never on re-rendered input.
  **Issue:** #72
- **Decision:** The cast panel's omniscient read is one direct call after the play view's own reads, not a call on the #41 pool. If it fails, Promote still shows on every non-player character: `is_player` comes from the play view's normal read. A promote GET for a character not in the cast redirects with "This story has no character <id>".
  **Why:** #48's reads are not pooled, so a one-key fan-out would only add a thread. The normal read already holds `is_player`, so a failed omniscient read only loses the "Promoted →" links. The plan had no wording for an unknown character id.
  **Issue:** #72
- **Decision:** The add form's images use a new `StoryImageFormSet`, which is #71's image-slot formset with the cap message "At most 20 images", plus a `STORY_CHARACTER_SECTIONS` spec (`images`) registered with #71's picker and slot-row endpoints. A 409 `revision_conflict` re-renders with the hidden expected revision moved to `details.current_revision`.
  **Why:** #71's `ImageSlotFormSet` says "gallery images". Without moving the revision, the "check and submit again" line would conflict again on every submit.
  **Issue:** #72
- **Decision (drift):** A blank role-character version is left out of the write. It is not sent as `null`. A companion's role sends `version: null` (its pin) when the draft held none.
  **Why:** The plan says blank version = `null`. The Engine's save rules refuse an explicit `null` on a non-companion (`role_version_null`: "omit `version`"). Only a companion's locked role may name a `null` pin, and its slots must echo unchanged (`companion_slots_fixed`).
  **Issue:** #73
- **Decision:** The role selects #68 built (default output viewpoint, roles present) now offer the role rows' ids in the current form state. `EditSnapshot.roles` is gone. The snapshot gains the published characters and the eligibility ratings.
  **Why:** The cast is edited on the same form, so a role added or renamed before Save must be offered at once, as the holder and audience choices are.
  **Issue:** #73
- **Decision:** The editor reads content ratings once (retired included) and narrows the list twice with `kept_ratings`: non-retired plus the saved rating for the main select, and non-retired plus the saved eligibility ratings for the roles. The detail page reads ratings once for the card rating and the eligibility names.
  **Why:** Convention 13 allows at most one ratings read per request. Eligibility can keep several retired ratings, which `rating_choices(current=…)` cannot keep.
  **Issue:** #73
- **Decision:** `StartingState.seeded_memories` and `ScenarioRole.seeded_memories` are `list[Seed]`, not tuples. `RoleEligibility.age_range` and `ScenarioRole.name_overrides` are raw `Any`.
  **Why:** The model parser and the contract test support lists only. The schema types `age_range` as a two-item `prefixItems` tuple. `name_overrides` is edited as a JSON box.
  **Issue:** #73
- **Decision:** A saved role character that the published list lacks is kept as "<id> (missing)". A posted holder that is not a current role or `world` is kept as "<value> (missing)", so the Engine's `unknown_role` error can land on it.
  **Why:** Without a read per id, the Console cannot tell an archived character from a never-published or deleted one. Without the kept option, the select would refuse the value before any call.
  **Issue:** #73
- **Decision:** #68's companion-name lookup moves to `console/catalog/character_labels.py` (`character_labels`, `CharacterLabel` with a new `archived` flag), and `companion_labels` stays as an alias. The detail page's World seeds section now sits right after Cast, because `_cast_table.html` renders both. The catalog test `engine` fixture answers `GET /catalog/characters` by default.
  **Why:** The cast table names role characters with the same one-read-per-id lookup, and it needs to know whether each one is archived. #74 reuses the partial whole. Every scenario edit page now reads the published characters.
  **Issue:** #73
- **Decision (drift):** The Console treats a companion's `character_not_published` as "Publish *Character* first" in two cases: a top-level 422 code (what the plan names, tested with the generic `Error` envelope), and an error entry inside a 422 `publish_invalid` report. The second is how the Engine actually refuses it.
  **Why:** `PublishScenarios.publish_alone` validates the repinned draft, so the code arrives as a report entry. The schema's publish 422 enum does not list `character_not_published`.
  **Issue:** #74
- **Decision:** On the edit page, Save keeps #68's `name="op" value="save"`. Save & publish is `name="action" value="publish"`. The two idempotency keys are `idempotency_key` (save) and `publish_idempotency_key` (publish, as in #66), not `idem_save`/`idem_publish`. `publish_scenario` takes an `idempotency_key` beyond the planned `(id, *, draft_revision)`.
  **Why:** Existing editor tests and helpers post `op=save` and `idempotency_key`. The publish endpoint takes `Idempotency-Key` (#43 per-render rule).
  **Issue:** #74
- **Decision:** A `publish_invalid` from Save & publish redirects to `edit/?report=<pk>`. That GET reads the draft fresh, places the report's errors at fields (`ScenarioEditor.place_report`) and shows the report in `#report`. Other refusals after a save (`rating_retired`, `entry_archived`, `character_not_published`, any other) re-render from a fresh draft read. Before placing errors, the editor validates quietly, so the Console's own checks of an unedited draft are not shown.
  **Why:** This is the "fresh draft read" the plan asks for, and the same report-from-row path #66 uses for characters. After a save the posted snapshot is stale, so re-rendering it would make the next save a 409.
  **Issue:** #74
- **Decision:** Validate and Publish post a signed `facts` token (scenario name, role → catalog-character map, and a companion's character and name). The Validate page title, the report's role-character links and the "pinned to" flash use it without extra Engine reads. Missing or tampered, it falls back to the id.
  **Why:** The plan's complement test needs Validate to make no write. Re-reading the scenario only to label the report adds a call per Validate.
  **Issue:** #74
- **Decision:** The character detail's companion section (#65 showed the companion's id, full views only) is now "Companion scenario" on every view, card included, from one `?projection=card` companion read. #69's companion edit page now names the character from the editor's single card read, where it showed the id before. Tests changed: `test_characters_browse` (×2), `test_lifecycle` (the edit-page banner) and `test_character_publish` (companion read counts now count `projection=full` only).
  **Why:** #74's AC and Discretion require it.
  **Issue:** #74
- **Decision:** `FakeEngine.respond` now replaces a `side_effect` on the same route. The catalog test fixture answers the companion route by projection (`companion_reads`: card or full draft). `_location.html` takes optional `entry_url`/`entry_label`, and an empty `anchors` collection now leaves locations as plain text (`default_if_none` in place of `default`).
  **Why:** One route serves both the card and the full companion reads. Version reports must show plain-text locations.
  **Issue:** #74
- **Decision:** `@operation` gains `listed=False`. With it, a second client method on an operation another method already lists stays out of `OPERATIONS`, but its calls are still recorded under their own kind. `create_story_from_scenario` uses it beside `create_story` (both `POST /stories`, both answering `StoryCreated`): its #44 rows have kind `create_story_from_scenario`, `entity_type="scenario"` and the scenario id. `StoryCreated.report` and `.impact` are now typed (`CreationReport`, `ImpactReport`), and the contract test covers both.
  **Why:** The registry is keyed by method and path, and the planned #44 kind is the method name. Without the flag, the new method would replace `create_story`'s registration.
  **Issue:** #75
- **Decision:** Scalar override widgets (enum, bool, int, and a text input for the string `default_output_viewpoint`) all send `null` when cleared to "(default)" or blank over a scenario value. A JSON box sends nothing when blank. Values identical to the scenario's are never sent. The plan named the `null` rule for bool only, and named no widget for a plain string.
  **Why:** One rule for every scalar lets the operator clear any setting back to the deployment default. A JSON box for a single role-id string would be awkward.
  **Issue:** #75
- **Decision:** If the pinned scenario cannot be re-read on POST (create, Create anyway, Back), the page shows the #43 panel. Every posted value is carried as hidden inputs with a Retry button: re-render for a create, the same op for confirm/back. The form itself is not shown, and nothing is sent. Persona options read `GET /personas?owner=<principal>` at limit 200, following cursors up to 500. The step-1 version select appears after "Choose" (`?pick=<id>`), and "Continue" goes to step 2.
  **Why:** Without the scenario's roles the form cannot be rebuilt, and no role list travels in a signed field (plan). Hidden inputs keep the input. Two GET stages need no JavaScript.
  **Issue:** #75
- **Decision:** `_two_stage.html` takes optional `confirm_op`, `cancel_op`/`cancel_label` and `hidden` pairs. The not-created page uses them: "Create anyway" posts `op=confirm` and "Back" posts `op=back`, both with the token. The partial's existing behaviour is unchanged. A shared `_impact_report.html` partial (context `impact`) is new.
  **Why:** The plan's "Back" posts the signed payload. It is not a Cancel link.
  **Issue:** #75
- **Decision:** "Offboard…" and the header's Offboarding link render only once `offboarding:new` / `offboarding:index` resolve (#77 builds them). Until then the principal page shows "Act as this principal" alone. Tests use a placeholder `offboarding` namespace in `tests/urls_m3.py`, and the `principals` placeholder there is gone.
  **Why:** #64's rule is "a link shows only once its URL exists; no stub pages". A button to a 404 would break that.
  **Issue:** #76
- **Decision:** `PrincipalLinks` overrides `from_json` and reads `PrincipalLookup` by hand. `lookup_principal` is registered with no response model. `OwnedPage`/`OwnedItem`/`CompanionRef`/`RelatedPage`/`RelatedItem` are contract-tested against their components, and the six type keys are checked against `OwnedEntities`/`RelatedEntities`. Unknown keys (top-level, type, page, item) go to `more` under dotted names (`owned.stories.<id>`).
  **Why:** The plan wants a type the Engine leaves out shown as "Not returned by the Engine" and unknown keys shown in "More". A field-by-field model would refuse a missing type outright.
  **Issue:** #76
- **Decision:** `cursor_invalid` is retried once with every cursor dropped, and only when cursors were sent. With no cursors, the error panel shows straight away.
  **Why:** Retrying the same request without cursors would only fail the same way again.
  **Issue:** #76
- **Decision:** The fake Engine matches a request's raw (percent-encoded) path as well as the decoded one. A principal with `/` travels as `%2F`, and the decoded path then has an extra segment. Owned items whose type has no Console page yet (engine profiles) show the name plus the id in `<code>`, unlinked.
  **Why:** The Engine routes `{principal_id:path}`, but the fake's `[^/]+` template did not. With no page to link, the id is still identifying.
  **Issue:** #76
- **Decision:** `@operation(entity=…)` now also takes `"principal"` (`console.entities.RecordedEntity` = `EntityType | "principal"`, `PRINCIPAL_ENTITY`). `offboard` is recorded with `entity_type="principal"`, `entity_id=<principal>`. The registry itself is unchanged, so the operations log shows a principal entity's id as `<code>`.
  **Why:** The plan records offboarding (and #86's erasure) on the principal, but `EntityType` named only the six Engine entity types.
  **Issue:** #77
- **Decision:** The start's signed payload is `twostage.sign("offboard", principal, body, base)`. `body` is the full request with `dry_run: false` and `ignore_warnings`. `base` holds `config_revision`, `operator`, `acting`, `key`, and the preview's `counts` and `warnings`. The start reads only this payload. The partial's hidden `idempotency_key` is ignored. The counts and warnings let a failed start (409 in progress, 503 retry, other panels) re-render the preview without another Engine call.
  **Why:** `twostage.sign` takes kind, target, body and base. The plan's extra fields fit in `base`, and the preview needs the plan to re-render.
  **Issue:** #77
- **Decision:** A field refusal on the start (`principal_reserved`, `transfer_target_*`) returns to the form, rebuilt from the signed body with the error at its field. The preview has no fields to place it on. An unreachable Engine on start is treated like a 503 retry: the same payload and key are re-offered as "Retry".
  **Why:** The plan places these codes "at their fields". A replay under the same key is safe and writes nothing new.
  **Issue:** #77
- **Decision:** The terminal poll settles the #44 row through `job_poll.settle_job` with `response=None`, so the row keeps the 202's answer. The job page reads its "Started over these warnings" from that row. A `failed` job's error code is `error.class`. Shared polling lives in `console/job_poll.py` (`poll_failure`, `poll_context`, `settle_job`) and `templates/console/_job_poll.html`, for #80 and #86.
  **Why:** Replacing the stored response with the job body would lose the confirmed warnings. No M3 polling helper existed yet.
  **Issue:** #77
- **Decision:** In report lines, an object entry's `id` links by its own `entity_type` when it has one (relations entries carry it). Otherwise it links by the section's type key. `relations` name lists show as text. `remaining` renders the Engine's `{owned, owned_counts, related, related_counts}` as "Still owned" / "Still related" per type, with ids linked. The job page answers 404 with the "expired" text when the Engine has no such job. A `<ulid:…>` path converter (`console.ids.UlidConverter`) makes any other id a Django 404 with zero calls.
  **Why:** The Engine's actual report shapes (src/epistrel/core/offboarding.py, principals.Remaining) are more specific than the plan's generic wording.
  **Issue:** #77
- **Decision:** The profile list's URL name is `profiles:index`, not the plan's `profiles:list` (`profiles:detail` and `profiles:versions` as planned).
  **Why:** #64 convention 20 makes each root's landing page `<ns>:index`, and the nav (`console/context_processors.py`) already shows "Profiles" once `profiles:index` resolves. #64's conventions take precedence over a story's lines.
  **Issue:** #78
- **Decision (drift):** `EngineProfile` replaces #68's `EngineProfileSummary` (the select and #75's creation report use it unchanged). `settings` stays `dict[str, Any]`, and the contract test widens `EngineProfile.settings` to any object, the way it already widens `EntryLocation.media_id`. All four profile reads are registered with response models.
  **Why:** The recorded schema types `settings` as the modules delivered when it was recorded (`additionalProperties: false`). The Console must show whatever this deployment's `/engine-profiles/schema` describes, so it can't hold the stricter shape.
  **Issue:** #78
- **Decision:** `tests/fixtures/profile_schema.json` comes from the Engine source (`epistrel.core.profiles.store.profile_schema` with the Engine's own `Purposes`, Engine @ b4adb1a), serialised through `ProfileSchemaBody`, not from a running deployment. It includes the modules the Engine delivers past M3 (`character_knowledge`, `perception`, `multi_character`).
  **Why:** No live Engine was available. The function is exactly what the endpoint returns.
  **Issue:** #78
- **Decision:** On the list, a deleted deployment default is detected by one extra `GET /engine-profiles/{default}`. This happens only when "Show deleted" is off and the default isn't among the listed rows. A failed read adds no note. "Start over" shows under the error panel only when a cursor was sent.
  **Why:** With deleted profiles hidden, the list itself can't say whether the default is deleted.
  **Issue:** #78
- **Decision:** A map entry and every setting under it take the map path's tags (e.g. `models.purposes.<purpose>.model` shows `models.purposes`' tags). An object node whose value is `null` (a nullable object such as `params`) renders as one leaf showing `null`, without descending.
  **Why:** The Engine tags only the map path. Descending into a `null` object would show children that this version explicitly cleared as "not set".
  **Issue:** #78
- **Decision:** `/stories/?profile=` reuses the per-story reads for the cast column, so it makes no second sequential read. Its "couldn't be checked" note is singular for one story. The unfiltered list is unchanged.
  **Why:** Reading every story once on the pool is what the filter needs anyway.
  **Issue:** #78
- **Decision (drift):** `OwnerTransfer` and `RelationRecord` match both the story and the non-story bodies. They do this through the contract test's widening (`_widened`): each of the four bodies gets both revisions, nullable and not required. A test checks that the widening touches only the revisions. `Relations` and `RelationItem` carry every `RelationsBody`/`RelationItem` field, including `entity_type`/`entity_id`.
  **Why:** The schema has separate bodies with one required revision each. The plan wants one model per shape with optional revisions. The widening keeps every route under the contract check, as #64 and #78 already do for their shared models.
  **Issue:** #79
- **Decision:** The four ownership client methods take `(entity_type, entity_id, …)` and are not `@operation`-decorated. Instead, `console/engine/client.py` registers all 24 routes (6 types × 4) in `OPERATIONS`, each naming its own entity, and marks each call with the matching registration (`OWNERSHIP_OPERATIONS`, `_ownership_call`). The #44 row's kind is the method name; its entity is the type and id.
  **Why:** `@operation` registers one route per method. One method serving six routes needs one registration per route so the contract test checks each one and the log records the right entity.
  **Issue:** #79
- **Decision:** For a stale-form 422 `companion_follows_character`, the character id comes from the error's `details.character_id`. The Engine always sends it. The scenario is re-read only when that id is missing. The message is #69's `companion_message`.
  **Why:** This saves a read on the common path. The fallback still follows the plan.
  **Issue:** #79
- **Decision:** The panel's refusal dataclass (`console.views.ownership.Refusal`) has the same shape as `console.views.story_settings.Refusal`, minus violations. It is rendered with the same `settings/_refusal.html`. Importing the original would create an import cycle: play view → ownership → story_settings → play view.
  **Why:** Avoids the cycle while keeping one template.
  **Issue:** #79
- **Decision:** The play view's panel sits after the story header's action links, before the play columns. Writes return to the GET URL of the play view (`stories:detail` when the page was rendered by a POST, such as a turn). The story's 423 line adds the `Operation` fact (from `details.operation_id`) under the Engine's error line.
  **Why:** A `next` pointing at a POST-only URL would 405.
  **Issue:** #79
- **Decision:** The transfer target is remembered in `KnownPrincipal` on the confirming POST, not on the GET that renders the confirm page. The relation principal is remembered on the add POST.
  **Why:** #64 says principals are remembered from valid POST forms. A GET that only checks the input should have no side effects.
  **Issue:** #79
- **Decision:** Every test fixture that renders an owned entity's page (catalog, play, timeline, history, stories, engine panel, personas, media, profiles, story characters, story-from-scenario) answers every relations read by default (`tests/fakes/ownership.py answer_relations`). One existing test (`test_one_render_posted_twice_sends_the_same_key`) drops the panel before picking the turn form's idempotency key, because the panel's add form carries its own key.
  **Why:** Every owned page now reads its relations. Without a default answer, the fake Engine fails every older page test.
  **Issue:** #79
- **Decision:** #53's `ReactivationCheck` model becomes `RatingCheckStarted` (the Engine's `RatingCheckStartedBody`, which the rating PUT's 202 also answers); `ReactivationCheck` stays as an alias so #53's code is unchanged.
  **Why:** One schema component, one model; the story names `RatingCheckStarted`.
  **Issue:** #80
- **Decision:** The check page's poll partial reads only the check, so the target rating's name and listed version travel as display-only query parameters on the poll URL (`t_name`, `t_vid`, `t_v`, like #43's `name=`). Other query parameters (#81's `op`) are kept on the poll URL. The pause banner's poll URL carries `cause=` for the same reason.
  **Why:** The applied line ("Rating changed to X", integer version) needs names the check body does not hold, and the planner forbids extra reads per poll.
  **Issue:** #80
- **Decision:** Choosing the current rating is detected from a plain hidden `current` field (the rating the page showed), so it makes zero calls; a forged value can only skip a write. A POST naming any other rating re-reads the story, list and details before deciding relax or tighten.
  **Why:** The test plan asks for zero calls when the current rating is chosen, and a fresh recompute for every real change.
  **Issue:** #80
- **Decision:** While paused, the play view also disables the ingest tab (with the turn form's note), and every #53 control, Fork included, on the transcript, the header's Undo rewind and the history page's Variants panel. Add character and Rebuild projections stay enabled (the Engine answers for them). #45's paused note on the story actions now reads "Paused while the check runs" (#80's wording), and its list test follows.
  **Why:** The AC says the play view's mutation controls are disabled, and ingest changes the story's truth like a turn. The Engine refuses forks of a paused story with `story_paused`.
  **Issue:** #80
- **Decision:** A 409 `story_paused` refusal is rendered by one helper (`console.story_pause.paused_refusal`) everywhere: #48's turn inspector (and through it #53's refusal page and the ingest tab) and #54's settings refusals. It takes the story id from the error's `details.story_id` when no other story id is given. Inspector and settings `Refusal` gained a `notice_url` so the line links to the check.
  **Why:** The Engine's `StoryPaused` details carry `story_id`, `job_id` and `progress`, and the inspector builder has no story id of its own.
  **Issue:** #80
- **Decision:** A refused check settles the #44 row with the full check body as its response; the violations model drops every key it does not know, so the stored body never holds violation text either.
  **Why:** The planner says to store the full body. Storing the parsed model keeps the never-the-text guarantee for the operations log too.
  **Issue:** #80
- **Decision (Rule 2):** A replayed job 202 (`Idempotent-Replay: true` with a `job_id`) is now recorded `ok` (`replayed`), not as a second `queued` row. It still carries its `job_id`, and `complete_job` settles only the first call's `queued` row. This applies to the rating PUT (#80) and offboarding too.
  **Why:** The plan says a replayed 202 makes no second queued row. Before this change, a replay left a `queued` row that no poll would ever settle.
  **Issue:** #81
- **Decision:** The client cannot log, so the 202's `Location` is kept on the client as `last_location`, like `last_operation_id`. The timeline view logs a `Location` that disagrees with the body's `job_id` at WARNING. The redirect always uses the `job_id`.
  **Why:** `RatingCheckStarted` mirrors the schema component, so adding a header field to it would fail the contract test.
  **Issue:** #81
- **Decision:** Untruncate and fork now take `timeout=` (default 60 s, the turn timeout), as swap already did. Fork no longer uses #43's 30 s fork timeout.
  **Why:** The planner says the three POSTs always use the turn timeout (#64 convention 18).
  **Issue:** #81
- **Decision:** The reactivation page's `<h1>` is "Guardrail re-check — <story>". The operation heading ("Undo rewind is waiting for a guardrail re-check", "… applied after re-check", "… refused by re-check", "… could not be completed", "…: re-check ended (`status`)") is an `<h2>` inside the polled partial, so it changes when the check ends. With no op, the waiting heading is the planner's generic one and the terminal headings use "The edit" as their subject. The pause banner writes the op in lowercase ("before undo rewind").
  **Why:** The poll swaps only the partial, so a heading in the page block would stay "waiting" after the check ended.
  **Issue:** #81
- **Decision:** I added a "Check" link to the #44 row detail for story rows that have a `job_id` and are kind `set_story_content_rating`, `untruncate`, `swap` or `fork`. The link targets `entity_id`, so a reactivated fork's row (whose `story_id` is the new story) still links to the source's check. Reactivation rows carry `?op=`.
  **Why:** The planner's second way back to a running check.
  **Issue:** #81
- **Decision:** I moved #80's violations table and #53's "Forked from" banner into partials (`stories/_check_violations.html`, `play/_fork_banner.html`), so the reactivation page reuses them. The reactivated result fields render from a `ResultRow` list. #53 has no result partials, because its results are flash lines.
  **Why:** The reactivation page shows the same violations table and the same banner. Reusing the partials avoids keeping two copies.
  **Issue:** #81
- **Decision:** `suggest_ratings(entry_type, entry_id, *, version, include_retired, timeout)` is one client method with two registrations (`SUGGESTION_OPERATIONS`, built the way #79 builds its ownership operations), each recorded under its entry with kind `rating_suggestion`. The method name differs from the recorded kind, so the method sets the kind on its `Call`.
  **Why:** The story asks for one method that takes the entry type, and for the kind `rating_suggestion` in the operation log. `@operation` registers one path and uses the method name as the kind.
  **Issue:** #82
- **Decision (drift):** The turn's `ModelCall` model already exists, so the suggestion's model calls parse into `SuggestionModelCall`, with every field optional. `RatingSuggestion.model_calls` stays raw (`list[dict[str, Any]]`, as the schema types it), and `.calls()` reads it. `Element.location` reuses #64's `EntryLocation`, which already parses `ElementLocation`. `SuggestionRow.error` and `Element.kind` are open enums, so a new code renders raw and doesn't fail the answer.
  **Why:** Two classes can't share a name. The schema types `model_calls` items as free-form objects.
  **Issue:** #82
- **Decision (drift):** The trigger and "Run again" use `hx-disable="find button"` (the htmx 4 name for the planner's `hx-disabled-elt`) with `hx-sync="this:drop"`. They also set `hx-config="timeout:3600s"`, so the browser waits longer than any Console timeout.
  **Why:** htmx 4's default request timeout is 60 s. A suggestion's Console timeout is 20 s per rating plus 10 s (90 s for the four seeded ratings), and the page can't know the rating count before the POST.
  **Issue:** #82
- **Decision:** The concern's element is worded and linked in Python (`element_html`), following the planner's per-kind wording. It doesn't use #64's `_location.html`, whose "entry X vN · field …" wording doesn't match. A kind the Console doesn't know renders as "<Kind> in `entry`", linked to the entry. A known policy shows its name, linked. An unknown policy shows its id, linked. When the policies read failed, the id shows unlinked.
  **Why:** The story's AC fixes the per-kind wording. `_location.html` keeps its shape for the validation reports.
  **Issue:** #82
- **Decision:** Retract also requires the open form's signed pre-fill (bound to story, message and acting principal); without a valid one it answers "This form expired — open it again" with zero Engine calls, like save. Under HTMX an expired form re-collapses the widget with that line; without JS it flashes and redirects to `next`. Without JS a successful retract redirects to the open form's page (flash "Rating retracted"), so the operator lands on the re-read history as the HTMX path shows it.
  **Why:** Retract sits only in the open form, which always carries the token, and the kept-form error path needs the pre-fill to re-render. The plan named the expired rule for save only.
  **Issue:** #83
- **Decision:** The signed pre-fill carries the whole rating read (history and context included) plus the tag list, text cap and whether the settings read worked, compressed. An invalid form, a 422 or a panel re-renders the kept form from it with zero extra reads.
  **Why:** The planner fixed "invalid form = zero Engine calls"; re-rendering the history and tag boxes needs those values.
  **Issue:** #83
- **Decision:** The feedback fake is a stateful helper, `tests/fakes/feedback.py` `FeedbackStore` (side effects on the three routes plus a `GET /admin/settings` answer), not new code in `FakeEngine` itself; every answer it serves is validated against the schema. Test tags follow the Engine's `feedback_tags` item pattern `^[a-z0-9_]{1,40}$`.
  **Why:** Same pattern as #79's `tests/fakes/ownership.py`; the generic fake stays schema-driven.
  **Issue:** #83
- **Decision:** In #52's history the colspan `<tr class="feedback-row">` is rendered under each item that carries a control (not under every item); the context block shows as its own list below the History `<details>`, only while a rating exists.
  **Why:** Rows without a control would be empty. The plan hid the context without a rating but didn't place it; keeping it outside the closed History keeps "the configuration it was given under" visible.
  **Issue:** #83
- **Decision (drift):** #44 has no "summary" column, so the planned summaries ("held for confirmation" on a 200 `applied:false` PUT; the source id and version on a copy) aren't written anywhere. The row's stored response (`applied: false`) and request path (with `?version=`) carry the same facts. A copy's row names the new copy, or the source profile when the copy failed, through a new `@operation(entity_id="response:id|profile_id")` fallback syntax (`|` tries each source in order).
  **Why:** Adding a column is a #44 schema change the story doesn't plan. The fallback is the smallest extension of #64's entity rule that gives the planned entity ids.
  **Issue:** #84
- **Decision:** The write views live in a new `console/views/profile_edit.py` (new, edit, apply, copy, delete) and `console/views/profile_defaults.py`, beside #78's browse views in `console/views/profiles.py`. The schema-generated form is `console/forms/profile_schema_form.py` `SchemaSettingsForm(data, *, schema, root, stored, defaults, mode)`, rooted at the body key so that #85 can use `root="overrides"`. Field names are `<root>__<path…>`, and the "null" checkbox is `null__<field name>`.
  **Why:** `profiles.py` would have more than doubled. Rooting field names at the body key keeps settings fields from colliding with `name`/`owner`/`action`, and turns `body.<root>.x.y` paths into a prefix strip.
  **Issue:** #84
- **Decision:** Validate on create/edit and the impact page's "Apply anyway" reuse #64's `twostage.sign` for both the page facts (kind `profile-form`: loaded version, name, settings, the deployment's `profile_defaults` and default id, schema digest) and the confirmation (kind `profile-apply`: body, the page facts, the posted form data). That way a 422 after "Apply anyway" rebuilds the edit form with the input in place. The deployment `profile_defaults` used for the "default: X" labels travel in the facts, so an unchanged or invalid POST makes zero Engine calls once the schema is cached.
  **Why:** The plan fixes "no change → zero calls" and "invalid → zero calls". Re-reading `/admin/settings` on every POST would break both.
  **Issue:** #84
- **Decision:** Choices the plan left open: (a) in `defaults` mode a JSON subtree (`models.purposes`) sends only its changed leaves, and removing a key from it is a form error ("Keys can't be removed from profile defaults"), because merge semantics there can't express removal (`null` is a value); (b) a non-nullable field cleared in `defaults` mode says "Enter a value (profile defaults can't be left unset)", and a nullable one says the planned "Tick “null” to set null"; (c) an object node that is itself nullable maps to a JSON textarea; (d) a 422 `invalid_profile` naming only stories shows the stories panel with no "Not valid" banner; (e) number inputs carry `min`/`max`, and the form is `novalidate` so the Console's own rules (an unchanged out-of-bounds stored value only warns) aren't pre-empted by the browser.
  **Why:** Each follows the plan's stated intent where it was silent.
  **Issue:** #84
- **Decision:** `_impact_report.html` is rewritten to the planned context (`impact`, optional `story_labels`, `mode`, `apply_url`, `signed_payload`, `cancel_url`, `idempotency_key`, `show_apply`), with rows built by `console/impact.py` (`{% load impact %}` `impact_rows`/`impact_more`/`short_fp`/`scenario_version_url`). #75's callers pass only `impact` and still render, read-only.
  **Why:** Planned shared partial for #84/#85; #75's tests only assert the "Impact report" heading.
  **Issue:** #84
- **Decision (Rule 1):** `console.impact.short_fingerprint` strips the `sha256:` prefix before taking 12 characters (it took the first 12 of the raw value, so a real Engine fingerprint showed as `sha256:xxxxx`). Regression test in `tests/test_story_config.py`.
  **Why:** #85's discretion fixes the short form as the first 12 hex digits after `sha256:`; #84's tests used bare hex so the bug never showed.
  **Issue:** #85
- **Decision:** The overrides form is `console/forms/story_overrides.py` `OverridesForm`, a subclass of #84's `SchemaSettingsForm` (root `overrides`, `other=True`) rather than a third `mode`: it overrides `default_of`/`default_text` ("inherits: X (profile|default)"), `_add_leaf` (tri-state/true-false booleans, a `remove__<field>` box, no `null__` box), `clean` (the "Tick 'remove override'" error) and `_strip` (also strips `settings.` so `body.settings.<path>` from the PATCH lands on the field), and adds `patch()`.
  **Why:** Keeps #84's two modes untouched; the override rules differ in every hook.
  **Issue:** #85
- **Decision:** The recorded `impact-preview` call runs in the request thread; only the reads (profile version, profiles list, schema, story, archived check) run on #41's pool.
  **Why:** A recorded call made from a pool thread writes its #44 row on another DB connection, which the test transaction (and a request's atomicity) doesn't see.
  **Issue:** #85
- **Decision:** `QualityStatus` keeps `status` only (no `extra` dict).
  **Why:** The recorded schema's `QualityStatus` has only `status`; an extra field fails the contract test, and unknown keys are already ignored by the lenient parser. M7 can add its fields when the Engine has them.
  **Issue:** #85
- **Decision:** "No change — the story already runs this configuration" is flashed when the applied result's overrides revision, profile id and version all equal those the preview was signed against; "keeps its previous configuration" when `upgrade_unavailable` and the result's fingerprint equals the write's own impact `fingerprint_before`; otherwise "Applied — fingerprint <short>; takes effect from the next turn".
  **Why:** The Engine moves no revision for a no-op; the fingerprint alone can't tell a quality-irrelevant change from none.
  **Issue:** #85
- **Decision:** The play header reads `GET …/effective-config` on every full play-page render (3 s, failures → "config unavailable"); every test fixture that renders the play page now calls `tests/fakes/story_config.answer_effective_config(fake)` beside #79's `answer_relations`.
  **Why:** The header block is part of #48's page; the fake Engine refuses unconfigured calls.
  **Issue:** #85
- **Decision:** #54's six superseded dials were already never built and its "Moved to engine profile overrides — see Config" note already existed with a hardcoded URL; #85 only makes `console.story_settings.config_url` reverse `stories:config` and adds a test that the link resolves to the Config page.
  **Why:** Replan 2026-10-24: #54 owns the note; #85 provides the target.
  **Issue:** #85
- **Decision (drift):** `ErasureResult.subject` is the Engine's `dict[str, str]` (`ErasureBody.subject` is an object of strings, not a `$ref` to `SubjectRef`), with a `subject_ref` property parsing it; `StoryScope`/`CatalogScope` are plain request dataclasses with `to_json()` (not response models).
  **Why:** The plan typed `subject: SubjectRef`; the contract test compares models with the recorded schema, and the schema says an untyped object.
  **Issue:** #86
- **Decision:** `@operation` gains `record_response` (the redacted JSON answer → what the row stores) and `record_about` (arguments → `RecordedAbout(entity_type, entity_id, story_id)`). `erase` uses them: every `candidates[].snippet` is stored as `"***"`, the entity is the subject (`catalog_character` → `character`, `persona` → `persona`, a story `character` → `story_character`, shown as `<code>`), and `story_id` is the scope's story only.
  **Why:** The plan asked for a per-method response override and the subject as entity. A story character stored as raw `character` would collide with the registry's catalog-character type and link to the wrong page.
  **Issue:** #86
- **Decision:** The signed erasure payload's `base` carries the offered ids, the real-run key, the operator and acting principal (as #77's start does), and the dry run's whole answer, snippets included, so a >1,000-ticked refusal re-renders the preview with its ticks and makes zero Engine calls.
  **Why:** The plan asked for zero calls on that refusal, and the snippets are never stored server-side, so the signed (not stored) token is the only place to keep them. It reaches only the operator's own browser, on the page that already shows them.
  **Issue:** #86
- **Decision:** `DATA_UPLOAD_MAX_NUMBER_FIELDS = 20_000` in settings.
  **Why (Rule 3):** Django's default of 1,000 fields refuses an erase POST with more than about 996 ticked candidates as a bare 400. Without the raise, the planned "At most 1,000 candidates per run" message could never show.
  **Issue:** #86
- **Decision:** "Zero calls" on a failed Console check means zero `POST /admin/erasure`. The form re-renders with its option lists, which takes reads. A refused real run (no payload, expired or tampered, ticked id not offered, more than 1,000) makes no Engine call at all.
  **Why:** Re-rendering the selects needs the lists. No erasure is reachable.
  **Issue:** #86
- **Decision:** Persona options read "<name> — <owner> · <id[:8]>" (no status). The story select is shown for every kind, labelled "(for 'This story')" unless it is required. `/erasure/` lists this Console's real runs from the operation log (the nav's `erasure:index`).
  **Why:** A persona has no status field. A catalog character or persona may also scope to a story, and only the kind and story selects re-render the section. The nav needs an index page, and the Engine has no read-back.
  **Issue:** #86
- **Decision:** 503 `erasure_pending` shows #43's generic panel.
  **Why:** #64's held-records wrapper (convention 16) is #87's to build; until then the generic panel is the convention.
  **Issue:** #86
- **Decision (drift):** The transcript changes live in `templates/console/history/_items.html` (and the play view's `play/_transcript.html`), not `templates/console/stories/_history_item.html`.
  **Why:** #52 never built a `_history_item.html`; its items partial is `history/_items.html`. `mark_erased` and the "erased" badge also go on the play transcript, so erased text reads the same on both.
  **Issue:** #87
- **Decision:** `ErasureStatus`, `ErasureBoundary` and `ReconcileResult` keep unknown keys in an `extra` property (`KeepsExtra.from_json`), not a dataclass field.
  **Why:** The contract test compares a model's dataclass fields with the schema's properties; an `extra` field would fail it. The property keeps the lenient "More" behaviour without widening the schema.
  **Issue:** #87
- **Decision:** `@operation(..., ended=True)` records a 202 as `ok` at once; `reconcile_erasures` sets it.
  **Why:** The operation log records every 202 as `queued` until a job poll settles it; reconcile answers 202 only when its run has ended and has no job, so its row would stay `queued` forever.
  **Issue:** #87
- **Decision:** The stories status filter's "Held" option sends `status=blocked_by_erasure`, and the empty list reads "No held stories.".
  **Why:** The Engine filters by its own status value; the option's label is the Console's wording. `?status=blocked_by_erasure` used to be ignored as unknown, so #45's fallback test now uses a truly unknown value.
  **Issue:** #87
- **Decision:** `HeldStoryMiddleware` covers every URL with a `story_id` kwarg, the Engine panel's rebuild (`/engine/rebuild/<story_id>/`) included.
  **Why:** The plan names any view with a `story_id`, and a held story's rebuild is the same held condition. #50's rebuild test now uses `database_unavailable` for its "other refusal" case.
  **Issue:** #87
- **Decision:** Erasure markers are dropped unless the history read asked for audit. A marker with no turn goes under turn 0, which is created on the oldest page (`next_before_turn` null) when the page has no turn-0 group.
  **Why:** Without this, the fallback router (audit-channel items in `items` when the answer has no `audit`) would show markers without omniscient audit. Turn 0 has no history row of its own when the opening has no items.
  **Issue:** #87
- **Decision:** The health reason now reads "erasure pending: N" (it was "erasure pending N"). It goes last in every state's reasons, healthy and unhealthy included.
  **Why:** The plan sets this wording and says to show it whatever the badge state is. #43's probe tests were updated to match.
  **Issue:** #87
- **Decision (orchestration):** Each M3 story was implemented by a fresh subagent working sequentially on `milestone/m3-catalog-product-surface`, from a shared brief plus a running list of the shared helpers earlier stories built. The orchestrator re-ran the full suite, ruff, ruff format and mypy after every story and checked the commit, push, ticked boxes and completion comment before starting the next.
  **Why:** 24 stories with 15–26 KB plans each; a fresh context per story keeps every plan line in view (same shape as M2).
  **Issue:** M3
- **Decision (Rule 3):** #64's commit reached the remote with one unformatted blank line in `console/engine/client.py` although the agent reported the format check clean; the orchestrator fixed it in `02a8599` and made a final format/lint/type gate mandatory in the brief for every later story.
  **Why:** The PR gate runs `ruff format --check`.
  **Issue:** #64
- **Decision (Rule 3):** CodeQL failed the milestone PR with 2 high (`py/redos`, `py/polynomial-redos` on `FIELD_NAME` in `console/forms/media_picker.py`) and 10 medium (`py/url-redirection`) alerts. The regex `^[a-z_]+(__[a-z_]+)*$` accepts exactly the strings `^[a-z_]+$` accepts, so it was replaced by the linear form. Every flagged redirect was already validated with `url_has_allowed_host_and_scheme`, but through a stored boolean or a helper (`_safe`), which code scanning does not recognise as a guard; the call now sits in the `if` itself in `confirm.safe_next`, `acting.back_to`, both `safe_back`s, scenarios' `_origin` and personas' `_local`. Behaviour is unchanged.
  **Why:** The merge gate requires CodeQL; dismissing alerts is the owner's call, restructuring an equivalent check is not.
  **Issue:** M3 (#64, #66, #68, #69, #71, #74)
- **Decision (Rule 1):** The live `engine-smoke` test `test_story_refusal_is_placed_beside_the_row_live` read the first `<option … selected>` on the new-story page, which since #64 is the nav's "Acting as" select, so it posted the principal as the rating. The search now starts at the rating select. The live tests are deselected locally, so only CI ran it.
  **Why:** A test defect introduced by #64's shell change, not a product bug.
  **Issue:** #64

## /n8-exec M3 (verification fixes) — 2026-10-25

- **Decision:** Ran `/n8-exec M3` as the fix pass for the nine bugs `/n8-verify M3` filed (#156–#164) after PR #155 merged, on `milestone/m3-verification-fixes` (same shape as M2's fix PR #133).
  **Why:** M3's stories were already merged; the open M3 work was the verify bugs. The epics (#9–#13) stay open for `/n8-verify`.
  **Issue:** #156–#164
- **Decision:** #156's catalog-group case uses a new `tests/urls_no_catalog.py` (the real routes without the four catalog prefixes), rather than `tests.urls_bare`, so the whole shell still renders.
  **Why:** `urls_bare` has no login or operations routes, so no signed-in page renders under it.
  **Issue:** #156
- **Decision:** #157 forces Django's temporary-file handler by lowering `FILE_UPLOAD_MAX_MEMORY_SIZE` below the fixture PNG's size, not by uploading a file above 2.5 MB. It records each `TemporaryUploadedFile` the request made and asserts each one is gone.
  **Why:** Same code path, with no multi-megabyte fixture; recording the temp files proves the handler actually ran.
  **Issue:** #157
- **Decision:** #158's running turn is a real queued turn (`POST …/respond` answers 202), and the page is then re-rendered from its restored waiting card ("Waiting for the queued turn to finish."). The test does not hand-build an operation row.
  **Why:** That is the state in which the turn form is disabled, so it is the state the Rate control must survive.
  **Issue:** #158
- **Decision:** Scenario reports render through `_validation_report.html` given an optional `scenario_report` (the `ShownReport`), and `templates/console/catalog/scenarios/_report.html` is deleted. With it, the partial shows the heading and headline, leaves out the empty "No errors."/"No warnings." lines (the headline covers them) and keeps the "Checks the Engine could not run: <label> (<code>)" wording. A `report_items` filter gives one entry loop for a `ShownReport`, a `ValidationReport` or a `CreationReport`. The parameter is `scenario_report`, not `shown`, because the creation report's own context already has `shown`, which leaked into the include.
  **Why:** #160 asks for one partial. The other callers render exactly as before.
  **Issue:** #160
- **Decision:** `_location.html` takes an optional `own_entry`. An entry about the page's own scenario shows no entry id or version (with nothing else to show, it reads "this entry"). A draft version reads "draft" everywhere, not "vdraft", so #75's promote warnings now say "entry <id> draft"; their two tests were updated.
  **Why (Rule 1):** "vdraft" was a shared-partial defect and showed on every caller, not only scenarios.
  **Issue:** #161
- **Decision:** A dry-run refusal from the form re-renders with the posted values (`form.choice()`). "Preview again" from a signed payload still uses `choice_of`.
  **Why:** Only the posted values carry the story a story character was picked from when the scope is catalog.
  **Issue:** #162
- **Decision:** `progress_text(..., ended=)`: with total 0, a check still running reads "starting…" under an indeterminate bar, and an ended one reads "nothing to check" with no bar. The same change applies to #81's reactivation check status.
  **Why (Rule 1):** Both partials share `progress_text` and had the same defect.
  **Issue:** #163
- **Decision:** `media_retired` at a slot reads "No such asset — choose another" when `details.reason` is `unknown`, and "This asset is retired — choose another" otherwise. This covers both the path-placed refusal (`place`, via `partial(self.place, reason=…)`) and the media-id-only one. Publish-report entries carry no `reason`, so they keep the retired wording.
  **Why:** The Engine answers an unknown id as `media_retired` with `reason: unknown`.
  **Issue:** #164

## /n8-exec M3 (verification fixes 2) — 2026-10-25

- **Decision:** M3 had already been executed (#155, #165 merged), so this run implemented only the open verify bug #166, on `milestone/m3-catalog-verify-fixes-2`. The tests drive the HTMX poll partial (`?op=swap`) after a held swap. The ended case uses `refused` with `progress {0, 0}`.
  **Why:** The poll partial reads only the check, so it isolates the progress line. `refused` is one of the endings the issue names. A scratch worktree with the #81 half of #165 reverse-applied fails the ended test, so it is a real gate. The starting test passes on both trees: the issue asks for it as the paired assertion, and that behaviour predates #165.
  **Issue:** #166

## /n8-exec M4 — 2026-10-25

- **Decision:** Executed M4 on one branch, `milestone/m4-inspectors`. A fresh agent ran each story in dependency order, sequentially, from a shared brief.
  **Why:** 22 detailed stories and a 7-minute suite. A fresh context per story keeps each one reading its full spec. Running them one at a time avoids conflicts in the shared `client.py`, `models.py`, `urls.py` and `base.html`.
  **Issue:** M4
- **Decision:** The confirm page's error line uses the existing red `errorlist` class (`<p class="errorlist confirm-error">`) rather than a new style rule.
  **Why:** base.html already styles `.errorlist` red. `.error` is styled only inside `ul.messages`.
  **Issue:** #134
- **Decision:** A 409 `module_off` with no `details.module` on a tab that has no rule (Overview, Predicates) reads "Needs a module that is off under this story's configuration" (`Unavailable("", None, …)`). Ledger's fallback module is `commitment`. `module_off_unavailable` takes an optional `effective`, so a `pipeline` answer can name the level the story runs.
  **Why:** The plan gives a fallback only for tabs with a rule. Ledger's own route is commitment's, and the rule already names `pipeline` before `build` runs on a Lite story.
  **Issue:** #135
- **Decision:** The greyed tab is `<span class="disabled" …>`, following pass 2's markup, not the earlier `class="tab disabled"`.
  **Why:** The two plan notes disagree. Pass 2 is the later, more detailed one, and its CSS rule is `nav.tabs span.disabled`.
  **Issue:** #135
- **Decision:** A 503 `erasure_pending` on the effective-config read returns `render_held` straight away, without the `get_story` read. Every other effective-config failure still runs `get_story`, so the header keeps the name.
  **Why:** The held wrapper shows only the story id, and the story read would get the same 503.
  **Issue:** #135
- **Decision:** The filters are in `console/templatetags/inspect_tags.py` (`code_spans`, `no_backticks`). The library is not named `inspect`.
  **Why:** `{% load inspect %}` would be a library sharing its name with the stdlib module. `console/inspect.py` keeps the name the plan gives it.
  **Issue:** #135
- **Decision:** The shell tests register every tab through a test urlconf (`tests/inspect/urls.py`). Its builds read `GET …/retrieve` and `GET …/inspect/ledger`, so the gate's zero-call rule and the 409 path can be tested before the tabs' own stories land.
  **Why:** #135 registers only `inspect:overview`. The real urlconf is used to show that unregistered tabs are absent.
  **Issue:** #135
- **Decision:** The Overview's fingerprint uses a new `overview_fingerprint` in `console/inspect.py` rather than #85's `short_fingerprint`.
  **Why:** The plan shows the `sha256:` prefix when present and "—" when empty. `short_fingerprint` drops the prefix and returns "", which the story header still relies on.
  **Issue:** #151
- **Decision:** A switch with an unexpected type (`<code>` verbatim) is tested by calling `module_rows` and rendering the template directly, not through the fake Engine.
  **Why:** The fake validates responses against the OpenAPI schema and refuses a mistyped switch, so a page-level test can't send one.
  **Issue:** #151
- **Decision:** A `verification:<level>` capability gets its label from a `verification` entry ("Verification") in `CAPABILITY_LABELS`. Any `<module>:<level>` with a known module reads "<Label> (<level>)". Budgets and models get "(always on)" only in their plain form.
  **Why:** The plan's general `<module>:<level>` rule needs a label for each module. No module is reported at a level in the always-on form.
  **Issue:** #151
- **Decision:** The hot-set models take the schema's names as they are (`HotSetBody`, `HotSetBudgets`, `HotCountsBody`, `HotSetPins`, `EpisodeBody`, `RetirementBody`), as the story's artifacts name them, not the shorter names some older models use.
  **Why:** The story names them so, and convention 12 says "named after the schema".
  **Issue:** #140
- **Decision:** Counts in the Memory tab's tables use a new shared `count` filter (`console_format`, thousands separators); budget values are still formatted in `console/hot_set.py`.
  **Why:** Convention 5 wants separators on every count, and later M4 tabs need the same; the story only ruled out filters for the budget values.
  **Issue:** #140
- **Decision:** Shared M4 helpers live in `console/inspect.py` (`short_id`, `history_turn_url`, `retrieval_url`) and `inspect_tags` (`engine_id`), with a `_character.html` partial for convention 2's names. `history_turn_url` links the active chain only (no variant yet).
  **Why:** Built once for the later Inspect stories; no #140 link names a variant.
  **Issue:** #140
- **Decision:** `tests/inspect/urls.py` now stubs only the tabs the real urlconf hasn't registered; a registered tab keeps its real view there.
  **Why:** Otherwise the stub would shadow the real Memory view under the test urlconf, which the Retrieval-link tests need.
  **Issue:** #140
- **Decision:** In Counts, "over budget" sits in the Hot cell beside the count; in Pins, the marker column is headed "Status" ("—" without a marker).
  **Why:** The story names the markers but not their cells or the column header.
  **Issue:** #140
- **Decision:** A 409 `module_off` from the hot-set read (the route doesn't declare one) goes up to the shell's greyed panel; `erasure_pending` goes up to #87's held wrapper. Every other failure stays inside the tab, so the Browse grid still renders.
  **Why:** That keeps the shell's behaviour and meets the story's per-section rule.
  **Issue:** #140
- **Decision:** Counts rows judge every character against `memories_per_character`, although the Engine scales a minor's budget, which the hot-set answer doesn't report. Filed #168.
  **Why:** The response doesn't carry the scaled budget, and adding it would change the API contract.
  **Issue:** #140
- **Decision:** `retrieve` sends `include_quarantined=true` (only when checked) and the form accepts any checked value (`1`, `on`), so #140's `include_quarantined=1` links work. `q` goes to the Engine exactly as typed, with no stripping.
  **Why:** The Engine's flag is a boolean query parameter. The AC says "exactly as entered", and the Engine checks `len(q)` on the raw text.
  **Issue:** #141
- **Decision:** With the cast known, a blank `actor` is filled with `cast.default_actor` before the form is bound, so the select shows the default and the id links keep `actor`. The player is still never sent.
  **Why:** This keeps the form sticky the way the history form fills in `limit`. The Engine call doesn't change.
  **Issue:** #141
- **Decision:** `explain`/`explain_variant` are not form fields yet. The `{% if explain_banner %}` slot includes `console/inspect/_explain_banner.html`, which #142 creates.
  **Why:** The plan says #142 owns them. A hidden pass-through with no meaning yet would only prejudge #142's design.
  **Issue:** #141
- **Decision:** With `skipped: ["episodic"]`, #135's reason renders inline above the results. The Resolution section still renders, and Candidates shows only its heading (no tables, no "retrieved no memories" line).
  **Why:** Convention 7 puts the notice above the empty results, and the AC says "instead of empty tables".
  **Issue:** #141
- **Decision:** "This question retrieved no memories." shows whenever the three tables hold no candidates at all, filter or not. The `memory` filter also accepts the shown short form with its leading "…".
  **Why:** Both are true whenever they apply, and the operator can then paste an id straight from a table.
  **Issue:** #141
- **Decision:** A mention's `<mark>` title carries the full entity id when the cast doesn't name it (the table shows the short id). The number goes in a `<sup>` after the mark, with `data-mention` on the mark.
  **Why:** The AC says "resolved name/id" for the title, and a title is where a full id belongs (convention 2). The mark's text stays exactly the span.
  **Issue:** #141
- **Decision:** Shared helpers added: `position_text` (convention 4) in `console/inspect.py`, the `decimal3` filter in `inspect_tags`, and the partials `_memory_table.html`/`_memory_row.html` (with convention 14's `actions`) plus `_score_legend.html`. `_character.html` lost its trailing newline so comma-joined lists don't show a stray space.
  **Why:** #142/#143/#145/#152 reuse the rows. Without the fix, participants rendered "Mara (player) , Tobin".
  **Issue:** #141
- **Decision:** With Retrieval registered, #140's link tests run against the real urlconf. A new `tests/inspect/urls_without_retrieval.py` keeps the "links omitted" path tested. `tests/inspect/urls.py` no longer lists a stub read for Retrieval.
  **Why:** The links now render for real, and #140's AC expects them there.
  **Issue:** #141
- **Decision:** `history_turn_url` stays in `console/inspect.py` (built by #140) and gains an optional `variant` for the `#turn-N-<variant>` fragment, instead of a second copy in `console/history.py`; `explain_query`/`explain_url` are in `console/history.py` as planned.
  **Why:** One convention-3 helper; existing callers keep the active-chain `-0` anchor.
  **Issue:** #142
- **Decision:** `explain_banner(cleaned_data, story_id, actor_label)` takes the story id and the asked actor's label rather than the story, and runs after the retrieve call.
  **Why:** The banner needs only those; running after the call means a 422 on `query.as_of` (which removes `as_of` from `cleaned_data`) drops the banner with no extra check.
  **Issue:** #142
- **Decision:** `result_inspector` now takes `story_id` first; just-played and logged-row inspectors get the history-anchor link via `_with_history_link`. An inspector with `error` or `error_line` gets none.
  **Why:** The history link needs the story; `error_line` marks a logged refusal, which the AC counts as an error.
  **Issue:** #142
- **Decision:** In the inspector the link sits in the revisions block after "Engine operation" (`<dt>Retrieval</dt>`); when the inspector has no revisions block (a transcript turn not played here) it renders as a line on its own.
  **Why:** A transcript turn played elsewhere has `revisions=False`, but the AC still gives it the link.
  **Issue:** #142
- **Decision:** `<mid>` uses the existing `<ulid:memory_id>` path converter (`console.ids.UlidConverter`) instead of a new `re_path`.
  **Why:** It is the same Engine ULID pattern, and it already 404s any other id before the view runs.
  **Issue:** #143
- **Decision:** `QuarantineForm.clean_reason` does the newline normalising and the length, blank and reserved checks, not the view.
  **Why:** The checks are still local, and the form re-renders the raw typed value either way. The field is still `CharField(strip=False, max_length=None)`, as planned.
  **Issue:** #143
- **Decision:** Shared mutation refusals on a memory write (404, `story_archived`, `revision_conflict`, `idempotency_conflict`, `erased_subject_referenced`, `story_paused`, `story_busy`) use short texts in `console/views/inspect_memory.py` (`REFUSALS`, `refusal_text`): an error flash for row actions, above the form on the quarantine page. Any other error goes to #43's panel. On the quarantine page that panel renders above the kept form.
  **Why:** #43 has no central code-to-text map. The paused and busy texts reuse `paused_refusal` and the busy wording from ownership.py.
  **Issue:** #143
- **Decision:** The pin-limit flash keeps the AC's literal backticks around `details.cap`. A scope other than `story`, or `character` without a `character_id`, uses the "Pin limit reached." fallback.
  **Why:** The AC gives the exact text, and the flash is plain text. Neither case is named, so there is no holder to show.
  **Issue:** #143
- **Decision:** `MemoryView.kind`, `membership` and `retrieval_tier` are open enums (`OPEN_ENUM`). `OPEN_ENUM`/`open_enum` moved to the top of `console/engine/models.py` so earlier models can use them.
  **Why:** The contract test needs enum-typed schema fields declared as open enums. The models that use them are defined above the old location.
  **Issue:** #143
- **Decision (Rule 3):** `tests/inspect/urls.py` builds `REAL` only from inspect routes named after a `Tab`.
  **Why:** The new `memory-*` routes in `inspect_patterns` aren't tabs, and `Tab(name)` raised on them.
  **Issue:** #143
- **Decision:** The explorer passes `actions_next` (the request's full path) next to `actions`, and the quarantine and release links get theirs from the `{% memory_action_url %}` tag (`console.inspect.memory_action_url`, which applies the 2,000-character fallback).
  **Why:** The row partial needs the explorer's path, and the fallback depends on each row's memory id.
  **Issue:** #143
- **Decision:** The Sanitize… link carries only `next` (#143's `memory_action_url`); the sanitize page reads the explorer's question (`q`, `as_of`, `actor`, `include_quarantined`) back out of `next`, and only when `next` is this story's Retrieval path.
  **Why:** `next` already holds exactly those parameters, so a second copy in the link would only drift. A `next` that fell back (over 2,000 characters) has no `q`, so the page says "Text not shown".
  **Issue:** #144
- **Decision:** The page re-asks the question with `RetrievalForm(..., cast=None)`, so a chosen actor is sent as `actor_character_id` even when it is the player. No `get_story` read is made.
  **Why:** Sending the player's id explicitly gives the same answer as leaving it out, and it saves a second read on every page load.
  **Issue:** #144
- **Decision:** `ConfirmPage` gains `buttons_template` (rendered after Confirm, before Cancel) for the sanitize confirm page's Edit button.
  **Why:** The planner puts Edit after Confirm, and `fields_template` renders before the buttons. Other confirm pages don't change.
  **Issue:** #144
- **Decision:** The Console's own refusal texts for sanitize are "Enter the span to replace." (empty span) and "At most 2,000 characters (this one has N)." (span or surrogate too long).
  **Why:** The AC doesn't give these texts. The wording follows the quarantine form and the question-length text.
  **Issue:** #144
- **Decision:** The redaction is two kind-keyed maps in `console/operation_log.py` (`REQUEST_REDUCERS` and `RESPONSE_REDUCERS`, only `sanitize_memory` for now). The client's per-operation `record_body` / `record_response` hooks aren't used for it.
  **Why:** This is where the planner put it, and the response reducer needs the span as it was sent, which `record_response` isn't given. The guardrail flag is computed on the answer before it is reduced.
  **Issue:** #144
- **Decision:** The detail page's "Re-reading the Engine's record isn't offered…" note takes the place of the Fetch button. It shows only on rows that name an Engine operation, because those are the only rows that ever had the button.
  **Why:** A refused sanitize has no Engine operation id, so it had nothing to re-read in the first place.
  **Issue:** #144
- **Decision:** Offer the shared scope only. The consolidate form has no scope choice and never sends `scope`. `consolidate_story` still takes `characters`.
  **Why:** This was the owner's round-two recommendation ("recs good"). In Engine M4 a characters scope finds no inputs.
  **Issue:** #145
- **Decision:** The Memory-tab form GETs `inspect:consolidate` carrying the tab's key. A valid GET renders the confirmed-mode confirm page directly through `render_confirm(..., idempotency_key=<carried key>)`, and the POST goes through `confirm_action(..., require_confirmed=True)`. New helper: `console.confirm.carried_idempotency_key(data)`, which `posted_idempotency_key` now uses.
  **Why:** `confirm_action` mints a fresh key on GET. The AC requires the Engine call to use the key the Memory tab rendered.
  **Issue:** #145
- **Decision:** An Engine refusal re-renders the consolidate page with a fresh key, values kept. `scope_mismatch` and `validation_error` answer 422. Shared refusals (convention 10 text via `refusal_text`) and #43's panel answer 200. A 2xx that is not a readable 202 flashes a warning and redirects to the Memory tab.
  **Why:** This matches #143/#144's quarantine and sanitize pages. The operator changes the window after a refusal, so a fresh key avoids `idempotency_conflict`.
  **Issue:** #145
- **Decision:** `_module_off` gains keyword-only `module` (the fallback when `details.module` is absent) and `back_label`. With `module` set, the panel also shows #135's "configuration changed — reload" line. `unavailable_page.html` takes `back_label` ("Back" by default).
  **Why:** The consolidate 409 must name `consolidation` as the fallback, not the Retrieval tab's `episodic`, and must link back to Memory.
  **Issue:** #145
- **Decision:** An ignored (malformed) `head` is not carried onto the confirm page. The Memory tab renders a value-less hidden `head` when the hot-set read failed.
  **Why:** The AC says a `head` that isn't an integer ≥ 1 is ignored. Carrying it forward would only re-ignore it.
  **Issue:** #145
- **Decision:** The just-played inspector (`result_inspector`) also offers the "Eval hooks & read set" block, besides `row_inspector` and `selected_inspector`.
  **Why:** The story is for any committed turn, and Demo 1 opens the block right after playing a turn. The block reads nothing until it is opened.
  **Issue:** #139
- **Decision:** `selected_inspector` now takes `story_id` first, and `row_inspector` takes an optional `story_id` (the page's). `Inspector.trace_shown` requires `trace_offered`, a story id, an operation id and no `error`/`error_line`.
  **Why:** The inspector needs the story id to build the trace URL. Keeping the error and id checks in one property means a builder only sets `trace_offered`.
  **Issue:** #139
- **Decision:** The lenient models (`EvalHooks`, `Verdict`, `RepairRung`, `ReadSet`, `QueryKey`) are plain frozen dataclasses with `parse`, not `Model` subclasses. A named field of the wrong type reads as missing and its raw value goes to `extras` ("More").
  **Why:** `Model.from_json` returns `None` for the whole object on one bad field. Convention 12 asks for skip-the-entry, keep-the-rest parsing.
  **Issue:** #139
- **Decision:** A read-set object that is a ULID shows as a convention-2 id. Any other string is a literal and shows verbatim in `<code>`. A `belief:<id>` holder shows like `utterance:<id>` ("belief · <id>").
  **Why:** The Engine's key `object` is an entity id, a literal or `*`, so shortening a literal would garble it. `belief:` holders come from Engine #159, which the story's Discretion doesn't list.
  **Issue:** #139
- **Decision:** Latency lists only the stages the Engine sent (in `STAGES` order, unknown ones after). A missing stage gets no row.
  **Why:** The Engine always sends all nine. A row for an absent stage would claim a 0 or "—" the Engine never reported.
  **Issue:** #139
- **Decision:** The held state's fragment uses a new `templates/console/stories/_held.html`, factored out of #87's `held.html`, with `held.held_context(...)`. Its Retry goes to the story page.
  **Why:** #87 had no held partial. A fragment can't retry itself in place, so Retry goes to the story page.
  **Issue:** #139
- **Decision:** The lazy block sets `hx-status:4xx`/`5xx="swap:none"` and shows the retry line on `htmx:error` and `htmx:response:error`.
  **Why:** The vendored htmx 4 swaps error responses by default. That would replace the retry line with an error page.
  **Issue:** #139
- **Decision:** When the unlock time has passed, the withheld note reads "…, and refused that read; the window should have closed — try again shortly." It doesn't name the viewpoint. A not-committed turn shows the read-set section only when a read set is present.
  **Why:** Both follow the AC's wording literally.
  **Issue:** #139
- **Decision:** On the Predicates tab, a predicate id whose row is on the page links `#p-<id>`; one the layer filter hides links the unfiltered tab (`?layer=all#p-<id>`); an id the registry doesn't hold is an unlinked short id.
  **Why:** A bare fragment to a row the filter hid would go nowhere. The AC asks for `#p-<id>` links and the Discretion asks for unfiltered links from elsewhere; this does both.
  **Issue:** #136
- **Decision:** The confirm page's checkboxes are a plain checkbox list in `_predicate_fields.html`, not a `MultipleChoiceField`. The POST sends the ticked values that are among the posted `offered` fields, in `offered` order.
  **Why:** With a `MultipleChoiceField`, a forged tick would make the form invalid. The AC says to drop it instead, and filtering by `offered` does that with less code.
  **Issue:** #136
- **Decision:** The approve-selected confirm question reads "Approve the ticked fields of the proposal for <name>? It proposes: a, b." and adds the "unticked are dropped" line. Approve all keeps the planned text.
  **Why:** The planned "Approve the whole proposal" wording is wrong for a subset. The Discretion only gives the extra line for selected mode.
  **Issue:** #136
- **Decision:** On the tab, Approve selected's checkboxes sit in the proposal table and join the GET form through the HTML `form="select-<id>"` attribute.
  **Why:** Each field's checkbox stays beside its current and proposed values, with no JavaScript.
  **Issue:** #136
- **Decision:** A 422 on decide re-renders the confirm page with a fresh key; a zero-tick POST keeps the posted key (no call was made). The result page on the POST response links "Link to this result" (`?report=<pk>`).
  **Why:** After a 422 the operator may change the ticks. A changed body sent with the same key would get `idempotency_conflict`. The link makes the result page reachable again, as convention 8 asks.
  **Issue:** #136
- **Decision:** `console.predicates.predicate_names(request, client, story_id)` returns `{}` on any failed read (logged). Canon, Ledger, the fact form and beat traces use it for names; pages that also need the rows call `list_predicates` themselves.
  **Why:** Convention 2 says a failed name read degrades to ids and never fails the page.
  **Issue:** #136
- **Decision:** The Canon tab reads the full registry (`list_predicates`) itself instead of #136's `predicate_names`, since the predicate select needs each predicate's layer and the registry order.
  **Why:** `predicate_names` returns names only; it is still one registry read per page (convention 2).
  **Issue:** #137
- **Decision:** `Polarity`, `LiteralKind`, `Granularity` and `Visibility.scope` are `open_enum` string fields, not bare `str`.
  **Why:** The contract test refuses a bare `str` for a schema enum; `open_enum` keeps unknown values rendering raw, as the story asks.
  **Issue:** #137
- **Decision:** `_proposition.html` and `_fact_row.html` take the raw `Proposition` / `FactBody` and compute their cells through `{% load canon_tags %}` tags (`proposition_view`, `fact_view`); the row's "superseded by" comes from the `FactState` passed as `state` (`None` hides the State column).
  **Why:** #146 and #147 can include them with their own reads, with no view-model to build first.
  **Issue:** #137
- **Decision:** `cast_names` maps a character id to its label with " (player)" already appended; a new partial `_entity.html` renders an id from it.
  **Why:** The interface names one `cast_names` map; #140's `_character.html` needs a separate `is_player` flag.
  **Issue:** #137
- **Decision:** A record time that isn't `YYYY-MM-DDTHH:MM[:SS]` (or isn't a real date) is a field error, "Enter a date and time"; the minute is checked as ASCII digits (so "1.5" and "-1" are refused with the minute message).
  **Why:** The story gives no wording for a malformed datetime; the strict check keeps a bad value from reaching the Engine.
  **Issue:** #137
- **Decision:** A failed current-set read (any Engine error, including 422) makes the closure "closed · not confirmed" with the failed reason; the facts page itself still renders.
  **Why:** Discretion says "failed read → not confirmed"; there is no reason to fail the whole page over the secondary check.
  **Issue:** #137
- **Decision:** The unconfirmed notice reads "Couldn't confirm whether N closed version(s) is/are still closed: <reason>".
  **Why:** Discretion fixes the first half and says "with the reason"; the reason follows a colon.
  **Issue:** #137
- **Decision:** The lineage view's reads run effective config → `get_story` → facts reads → `list_predicates`, not the planned config → facts → `get_story` → predicates.
  **Why:** #135's `inspect_page` reads the story before any tab's `build`; changing the shell for one view isn't worth it, and the registry read still comes after the facts reads and runs on every render the gate allows.
  **Issue:** #138
- **Decision:** With subject/predicate params whose lineage read lacks the fact, the view always makes one `find_fact` read; if it finds the fact under the same pair, a capped read gives the "beyond the first 2,000" message and a complete read gives the not-in-timeline one.
  **Why:** The AC asks for one `find_fact` when the fact isn't in the read; this order also lets a capped read tell "beyond" from "stale params".
  **Issue:** #138
- **Decision:** The incomplete-read warning is left out when the view shows the "beyond the first 2,000" message, and the header names the subject and predicate only once they are confirmed (the fact is in the lineage read, or `find_fact` returned it).
  **Why:** The two messages say the same thing, and stale params shouldn't be shown as the fact's subject.
  **Issue:** #138
- **Decision:** A capped lineage read's unconfirmed closures use a new state-rule reason `LINEAGE_CAPPED` ("More than 2,000 versions of this subject and predicate…") instead of #137's current-set "capped" wording. `lineage_states` reuses `fact_states`, with a stand-in current set (every version of the component when the read is complete).
  **Why:** The lineage view makes no current-set read, so #137's wording would be wrong; reusing `fact_states` keeps one state rule.
  **Issue:** #138
- **Decision:** 422 on a lineage read shows #43's generic panel in place of the rows; `viewpoint_unresolved` and 403 `omniscient_required` show "This read needs the omniscient viewpoint", the same as the Canon tab.
  **Why:** The lineage view has no form to place a field error on; this keeps convention 10's text the same as #137's.
  **Issue:** #138
- **Decision:** `_fact_row.html` and `_lineage_row.html` link "first closed by" ids to the lineage view (with the row's subject and predicate), as well as the id, supersedes and superseded-by ids. No other Console page renders fact ids yet (#139's read set holds entity ids), so no other links were added.
  **Why:** Convention 3 says every fact id links the lineage view; a truncated closer then gets the not-in-timeline message.
  **Issue:** #138
- **Decision:** A version whose `tx_from` doesn't parse has no tx bar ("—"); its text columns still render.
  **Why:** The Engine always sends ISO datetimes, but the page shouldn't fail on a bad one.
  **Issue:** #138
- **Decision:** The new-fact, supersede and result pages render inside #135's Inspect shell (Canon tab). `inspect_page` gained two small extension points: a build may name another template under `TEMPLATE_KEY`, and a build may return an `HttpResponse` (a redirect or the not-found page).
  **Why:** A greyed Canon tab then makes zero fact calls, and the page gets convention 2's `get_story` read for free. The result page replaces the form on the POST response without a second view.
  **Issue:** #146
- **Decision:** The result page's `?report=` URL is `inspect:canon-written` (`canon/written/?report=<pk>`). It accepts only `ok` `commit_fact` / `supersede_fact` rows of the story; for any other `pk` it renders convention 10's not-found line in the tab.
  **Why:** The story names convention 8's `?report=` but no URL. This follows #136's `decision_report`.
  **Issue:** #146
- **Decision:** With the registry read OK but no predicate chosen (or an id the registry doesn't hold), the new-fact page shows only the predicate select and a prompt. The main form, JSON box included, appears once a registry predicate is chosen.
  **Why:** The AC says the predicate comes first and gates the form. A free-text predicate exists only when the registry is unavailable.
  **Issue:** #146
- **Decision:** An entity-kind predicate with `set_object: true` offers both an entity id and a set. `any` offers entity id and literal, plus a set when `set_object` is true.
  **Why:** I read the Discretion's "entity id, or a set when set_object is true" as adding the set option, not replacing the entity one.
  **Issue:** #146
- **Decision:** `console.canon.locate_fact` returns `find_fact`'s version together with the page's `timeline_revision`, and `find_fact` now wraps it.
  **Why:** The supersede page's `fact_states` check runs `read_current_set`, which needs that revision for its race check. It still makes one id-only read.
  **Issue:** #146
- **Decision:** The supersede snapshot (`canon_supersede`, `twostage.sign`) holds the AC's identity fields in `body`, and in `base` the display-only carry-over values (visibility, lock, `anchored_to`), the Instant note and the pre-fill body. On POST the snapshot is checked before the shell runs, so a tampered or expired one renders a bare message page with zero Engine calls (the shell reads included). A snapshot for another fact or story counts as tampered.
  **Why:** The AC asks for zero calls and the read-only `<pre>` of the pre-fill body after a local failure, and nothing is re-read on POST.
  **Issue:** #146
- **Decision:** In a free qualifier row, a key with a blank value is not sent (as in a declared row). An undeclared predicate's stored qualifiers pre-fill the supersede rows with a type inferred from the JSON value (id-shaped string → `id`, list → `ids`).
  **Why:** The AC says nothing about these cases. This keeps both row modes consistent.
  **Issue:** #146
- **Decision:** Canon refusals reuse #143's `refusal_text` but override the memory-worded texts: `story_archived`, `revision_conflict` and `erased_subject_referenced` get canon wording (`CANON_REFUSALS`).
  **Why:** #143's texts mention memories.
  **Issue:** #146
- **Decision (Rule 2):** `base.html` renders a flash tagged `safe` without escaping. Only the `fact_superseded` flash uses the tag, and it is built with `format_html`.
  **Why:** The AC asks for a lineage link in that flash. Message storage drops `SafeString`, so the template has to know.
  **Issue:** #146
- **Decision:** The Lite goldens (`tests/fixtures/inspect/golden/`) were recorded in #147's own commit, from the branch's templates before any #147 edit, not in the branch's first commit.
  **Why:** The branch already had 13 commits when #147 ran. Against `origin/main`, `_items.html` differs only by #142's explain link, which a Lite page never shows, so the goldens match main's Lite markup. They cover the history page's items section and the play page's `#transcript` (turn controls and Rate links included), rendered through the views.
  **Issue:** #147
- **Decision:** `beat_trace(item, trace, state, *, story_id, cast_names, predicate_names, inspector_hint, open_trace)` takes the caller's state only when there is no trace (`not_loaded` or `unavailable`). With a trace, the state comes from the read: withheld is `unavailable`, no hooks is `no_hooks`, otherwise `loaded`. An ingested item is always `none`.
  **Why:** The AC lists the five states but not who decides each. This way the view never has to work out hook state itself.
  **Issue:** #147
- **Decision:** The repair line checks in this order: no beat gives "—", then any matching repair entries give their phrases, then no declared verdicts gives "None", otherwise "Not repaired".
  **Why:** The AC lists the wordings without an order. A repair entry for a beat whose verdicts aren't on the page is still worth showing.
  **Issue:** #147
- **Decision:** A detected claim's mark title and table use the verdict verbatim, "not checked" for null. The declared table shows "—" for `escalated` false or missing.
  **Why:** That is what the AC says ("escalated bold / —"). #139's turn-level table still tells false apart from missing.
  **Issue:** #147
- **Decision:** "M detected" counts every `detected` entry, including ones too malformed to parse. Those go to the beat's "More" ("detected N"), the same way skipped propositions do.
  **Why:** The AC says M counts every entry, and convention 13 keeps the raw values.
  **Issue:** #147
- **Decision:** `UtteranceLabels` keeps later Engine label fields (`classification`, `intent_corrected`, which Engine main added after M4) in `extras`. Those, or labels that don't parse, put the raw `labels` in "More".
  **Why:** The recorded schema has only the three labels. Engine main already sends more on omniscient reads.
  **Issue:** #147
- **Decision:** `console.turn_trace.declared_beat(claim_id) -> int | None` is the parse of `d<beat>.<n>`, and `beat_of` now uses it.
  **Why:** #147 needs the beat number, #139 needs the "beat N" text, and the two shouldn't parse it differently.
  **Issue:** #147
- **Decision:** `turn_traces` catches only `EngineClientError` from a worker and re-raises anything else. Errors are logged on the request thread after the wait.
  **Why:** The AC's failure reasons are all Engine errors. A Console bug should still surface. Only Engine reads run in the workers.
  **Issue:** #147
- **Decision:** #142's explain-retrieval test fixture now answers `GET /stories/{id}/predicates`. Its structured history page makes #147's one registry read.
  **Why:** The new read is in the AC. Without an answer the schema-validating fake fails the page.
  **Issue:** #147
- **Decision:** The Ledger tab's predicate and story-name reads run only after the ledger read answers; an invalid filter or a failed ledger read makes no registry read either.
  **Why:** Nothing on the page needs names without rows, and it keeps "zero ledger calls" pages to the shell's two reads.
  **Issue:** #148
- **Decision:** Cast names come from the shell's own `get_story` read (`ctx.story`), not a second read.
  **Why:** Convention 2 allows one `get_story` per page and `inspect_page` already makes it.
  **Issue:** #148
- **Decision:** The "Entries for thunks not on this page" table adds a Thunk column (the entry's `thunk_id`, short id); an entry with neither `thunk_id` nor `proposition_id` also lands there.
  **Why:** Without the thunk id those entries can't be told apart; the AC lists the other five columns only.
  **Issue:** #148
- **Decision:** The thunks table has a 12th column, "More", after Rejected bindings (the sub-rows use `colspan="12"`).
  **Why:** Convention 13's "More" (full id, sketch texts, span, parent fact, further candidates) needs a cell; the planned header list didn't name one.
  **Issue:** #148
- **Decision:** A hidden-fact entry the Engine returns on a page reached by `after_id` is left out, not shown.
  **Why:** The AC says the group renders on the first page only; the Engine shouldn't send them there anyway.
  **Issue:** #148
- **Decision:** `test_shell.py::test_other_errors_from_build_are_the_error_panel` now uses a 503 `database_unavailable` on the ledger route instead of a 404.
  **Why:** The real Ledger tab renders a 404 as convention 10's not-found line; a 503 still exercises #43's panel.
  **Issue:** #148
- **Decision:** Response models can type a field `datetime`: `_parse` reads an ISO 8601 string with an offset as an aware datetime (no offset fails the field), and the contract checker maps `datetime` to `string` `format: date-time`. The eval models use it for `from`/`to`/`created_at`.
  **Why:** The plan asks for aware datetimes in the model; every earlier model kept times as strings, so the model layer had no datetime type.
  **Issue:** #149
- **Decision:** The eval page's GET params are parsed by small dataclasses in `console/evals.py` (`TimeInput`, `Window`), not a Django form.
  **Why:** The prefill-only-when-the-key-is-absent rule and the "exactly as submitted" hidden carry-over are simpler on the raw QueryDict than through a bound form.
  **Issue:** #149
- **Decision:** The shadow section's "Next page" link pins `s_from`/`s_to` to the values that page sent (the prefill included); "First page" keeps every other param and shows whenever `s_after` is set, a refused cursor's 422 included.
  **Why:** With absent keys the prefill is "now", which moves between clicks; a cursor should page the same window.
  **Issue:** #149
- **Decision:** The echoed window renders to the minute (`2026-10-17 14:03 UTC`) and adds seconds only when the bound has them; sample `created_at` uses the shared `engine_time` (to the second).
  **Why:** The AC's window text is minute precision, but the Engine's own defaults carry seconds, which shouldn't be hidden.
  **Issue:** #149
- **Decision:** A null escalation rate reads "0 claims, 0 escalated · No claims checked in this window · reference 20.0%, not a gate"; a `s_story` id not in the story list gets its own selected option "…last8 (not in the list)" so it is still sent.
  **Why:** The AC fixes the words but not the separators; the extra option keeps a bookmarked or typed id from being dropped by the select.
  **Issue:** #149
- **Decision:** Added the shared `percent` filter to `console_format` (one decimal with `%`, "—" for non-numbers) and `local_moment` to `console/forms/inspect.py` (`record_moment` now wraps it).
  **Why:** No rate formatter existed yet; the datetime-local parse was private to the canon form.
  **Issue:** #149
