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

## License

Apache-2.0. See [LICENSE](LICENSE).
