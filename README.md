# Epistrel Console

A Django web app that serves as the human-facing test harness for the [Epistrel Engine](https://github.com/nathanpond/Epistrel): a place to drive and inspect the Engine's capabilities by hand. The Engine itself is a UI-less service; this repo is where its features get a test surface.

**Status:** early scaffold (stock Django project).

## Build and test

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync                              # create .venv and install dependencies
uv run python manage.py migrate      # set up the local SQLite database
uv run python manage.py runserver    # run on http://127.0.0.1:8000
uv run pytest                        # tests
uv run ruff check && uv run ruff format --check   # lint and format
uv run mypy                          # type check (strict, django-stubs)
```

Settings come from the environment: `DJANGO_DEBUG` (default `1`), `DJANGO_SECRET_KEY` (required when debug is off), `DJANGO_ALLOWED_HOSTS` (comma-separated).

## License

Apache-2.0. See [LICENSE](LICENSE).
