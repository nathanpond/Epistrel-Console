# Epistrel Console

A Django web app that serves as the human-facing test harness for the [Epistrel Engine](https://github.com/nathanpond/Epistrel): a place to drive and inspect the Engine's capabilities by hand. The Engine itself is a UI-less service; this repo is where its features get a test surface.

**Status:** early scaffold (stock Django project).

## Build and test

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync                              # create .venv and install dependencies
uv run pytest                        # tests
uv run ruff check && uv run ruff format --check   # lint and format
uv run mypy                          # type check (strict, django-stubs)
```

Lint, type check, tests and migrations run without any Engine configured.

### Dev setup

The Console needs a running Engine. Start one from the [Engine repo](https://github.com/nathanpond/Epistrel) with its own compose file, then run the Console against it:

```bash
# in the Engine repo
docker compose up -d                 # Engine on http://127.0.0.1:8000

# in this repo
uv run python manage.py migrate      # create the local SQLite database
CONSOLE_ENGINE_URL=http://127.0.0.1:8000 uv run python manage.py runserver
```

Open http://127.0.0.1:7739/. The Console runs on port 7739 so it never collides with the Engine (8000); `runserver <addr:port>` still overrides it. Without a valid `CONSOLE_ENGINE_URL`, `runserver` (and the WSGI/ASGI entry points) refuse to start and name the problem.

### Settings

All settings come from the process environment.

| Variable | Default | Meaning |
|---|---|---|
| `CONSOLE_ENGINE_URL` | — (required to serve) | The Engine's base URL: `http://` or `https://` with a host, no credentials, query or fragment. Trailing slashes are dropped. |
| `CONSOLE_ENGINE_SERVICE_KEY` | unset | The Engine service credential. Optional until the Engine enforces service auth; never logged or shown. |
| `DJANGO_DEBUG` | `1` | `1` for development; anything else turns debug off. |
| `CONSOLE_DATA_DIR` | the repo directory | Where the Console keeps its own data: `db.sqlite3` and, with debug off, the generated `secret_key`. A relative path resolves against the repo; created if missing. The image sets `/data`. |
| `DJANGO_SECRET_KEY` | dev fallback while debug is on; with debug off, generated on first start into `<CONSOLE_DATA_DIR>/secret_key` and reused | Set it to override the key in either mode. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `http://127.0.0.1:7739,http://localhost:7739` | Comma-separated origins allowed to POST. |
| `DJANGO_ALLOWED_HOSTS` | empty | Comma-separated host names. |

Any other variable starting with `CONSOLE_` is treated as a typo and refuses start.

## Run with Docker

`docker-compose.yml` runs the Console, the Engine's published `edge` image and the Postgres it needs. No `.env` or setup is needed.

```bash
docker compose up --build -d --wait --wait-timeout 120   # three healthy services
open http://127.0.0.1:7739/                              # Engine http://engine:8000 — Healthy
docker compose exec console python manage.py createsuperuser   # an admin login
docker compose pull engine && docker compose up -d --wait      # pick up a newer Engine edge
docker compose stop                                      # stop, keeping everything
docker compose down                                      # remove containers, keep data
docker compose down -v                                   # remove containers and wipe all data
```

- Only the Console is published, on `127.0.0.1:7739`; the Engine and Postgres are reachable only inside the stack. Nothing is reachable from other machines.
- The stack runs alongside the Engine repo's own compose stack (ports 8000 and 5432) without clashing.
- Nothing restarts by itself: the stack comes up only when you run `up`.
- Console data (its SQLite database and generated secret key) lives in the `console-data` volume and survives `down`; `down -v` wipes it, along with the Engine's database and media.
- `up --wait` exits non-zero if the Engine is unhealthy, even though the Console itself is up; the Console page then says why.
- The Engine runs with its compose's dev-only service credential and config key. They are fine for this localhost-only stack and nothing else.
- Every value in `docker-compose.yml` is a literal, so a `.env` cannot change any setting. The exception is the project name: the file's `name: epistrel-console` fixes it, but a `COMPOSE_PROJECT_NAME` in the environment or a `.env` overrides it, and then the volume names change too, so the stack comes up empty. Leave it unset to keep your data.

## License

Apache-2.0. See [LICENSE](LICENSE).
